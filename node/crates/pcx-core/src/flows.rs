//! Intérprete de flujos (`run_flow`): máquina de estados que corre dentro del actor, sin el LLM en cada toque.
//!
//! Transiciones por evento (`on`: tipo + igualdad de campos con rutas con punto) o por tiempo (`after_s`). Si un
//! evento y un temporizador caen en el mismo tick gana el evento (los eventos se despachan en `after()` del trabajo
//! que los generó, antes del próximo tick). Entre temporizadores gana el menor `after_s`. `after_s: 0` es inmediata:
//! máx. `MAX_IMMEDIATE_TRANSITIONS` seguidas, si no el flujo se detiene con `cause: "immediate_loop"`.
//! Las acciones llevan `request_id` derivado `"<request_id del run_flow>/<estado>/<i>"`.

use std::collections::BTreeSet;

use serde_json::{json, Value};

use crate::proto::*;
use crate::world::World;

pub(crate) struct FlowRun {
    pub flow: Flow,
    pub request_id: String,
    pub state: String,
    pub started_ns: i64,
    pub entered_ns: i64,
    pub widgets: BTreeSet<String>,
}

impl FlowRun {
    fn current(&self) -> Option<&FlowState> {
        self.flow.states.iter().find(|s| s.id == self.state)
    }
}

fn get_path<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(v, |d, part| d.as_object().and_then(|m| m.get(part)))
}

pub fn matches(f: &EventFilter, ev: &Event) -> bool {
    let data = serde_json::to_value(ev).unwrap_or_default();
    if data.get("type").and_then(Value::as_str) != Some(f.event.as_str()) {
        return false;
    }
    f.where_.iter().all(|(k, v)| get_path(&data, k) == Some(v))
}

impl World {
    pub(crate) fn flow_start(&mut self, flow: Flow, request_id: String) -> Result<Value, String> {
        let errs = check_flow(&flow);
        if !errs.is_empty() {
            return Err(format!("flujo inválido: {}", errs.join("; ")));
        }
        self.store.flow_save(&flow.id, &request_id, &serde_json::to_value(&flow).unwrap_or_default());
        let id = flow.id.clone();
        if self.runs.contains_key(&id) {
            self.flow_stop(&id, "restart");
        }
        let t = now_ns();
        let initial = flow.initial.clone();
        self.runs.insert(id.clone(), FlowRun { flow, request_id, state: initial.clone(), started_ns: t, entered_ns: t, widgets: BTreeSet::new() });
        self.flow_enter(&id, &initial, None, "start", 0);
        let state = self.runs.get(&id).map(|r| r.state.clone());
        Ok(json!({"flow_id": id, "state": state, "running": self.runs.contains_key(&id)}))
    }

    pub(crate) fn flow_stop(&mut self, flow_id: &str, cause: &str) -> Value {
        let Some(run) = self.runs.shift_remove(flow_id) else {
            return json!({"flow_id": flow_id, "running": false});
        };
        if run.flow.cleanup {
            let ids: Vec<String> = run.widgets.iter().cloned().collect();
            self.remove(&ids);
        }
        self.emit(
            Event::FlowStateChanged { flow_id: flow_id.into(), request_id: run.request_id, from_state: Some(run.state), to_state: None, cause: Some(cause.into()) },
            "core",
            None,
        );
        json!({"flow_id": flow_id, "running": false, "cause": cause})
    }

    fn flow_actions(&mut self, flow_id: &str, state: &str, actions: Vec<Action>) {
        let Some(rid) = self.runs.get(flow_id).map(|r| r.request_id.clone()) else { return };
        for (i, a) in actions.into_iter().enumerate() {
            let req = format!("{rid}/{state}/{i}");
            let res = match a {
                Action::Project { widgets } => self.project(widgets, false, Some(flow_id)).map(|_| ()),
                Action::Update { id, set } => self.apply(Command::Update { request_id: req, id, set }, Some(flow_id)).map(|_| ()),
                Action::Remove { ids } => self.apply(Command::Remove { request_id: req, ids }, Some(flow_id)).map(|_| ()),
                Action::Clear {} => self.apply(Command::Clear { request_id: req }, Some(flow_id)).map(|_| ()),
                Action::Emit { event, data } => {
                    self.emit(Event::FlowEmitted { flow_id: Some(flow_id.into()), request_id: Some(rid.clone()), event, data }, "core", None);
                    Ok(())
                }
                Action::HomeCall(body) => {
                    self.home_call(body, &req, Some(flow_id));
                    Ok(())
                }
            };
            if let Err(e) = res {
                // una acción mala no detiene el flujo
                let mut data = Json::new();
                data.insert("state".into(), state.into());
                data.insert("error".into(), e.into());
                self.emit(
                    Event::FlowEmitted { flow_id: Some(flow_id.into()), request_id: Some(rid.clone()), event: "action_error".into(), data },
                    "core",
                    None,
                );
            }
        }
    }

    fn flow_enter(&mut self, flow_id: &str, to: &str, from: Option<String>, cause: &str, depth: usize) {
        let Some(run) = self.runs.get_mut(flow_id) else { return };
        run.state = to.into();
        run.entered_ns = now_ns();
        let rid = run.request_id.clone();
        let Some(st) = run.current().cloned() else { return };
        self.emit(
            Event::FlowStateChanged { flow_id: flow_id.into(), request_id: rid, from_state: from, to_state: Some(to.into()), cause: Some(cause.into()) },
            "core",
            None,
        );
        self.flow_actions(flow_id, to, st.on_enter.clone());
        if !self.runs.contains_key(flow_id) {
            return;
        }
        if st.r#final {
            self.flow_stop(flow_id, "final");
            return;
        }
        if let Some(t) = st.transitions.iter().find(|t| t.on.is_none() && t.after_s == Some(0.0)).cloned() {
            if depth >= MAX_IMMEDIATE_TRANSITIONS {
                self.flow_stop(flow_id, "immediate_loop");
            } else {
                self.flow_fire(flow_id, t, "after_s", depth + 1);
            }
        }
    }

    fn flow_fire(&mut self, flow_id: &str, t: Transition, cause: &str, depth: usize) {
        let Some(from) = self.runs.get(flow_id).map(|r| r.state.clone()) else { return };
        self.flow_actions(flow_id, &from, t.actions.clone());
        if self.runs.contains_key(flow_id) {
            self.flow_enter(flow_id, &t.to, Some(from), cause, depth);
        }
    }

    pub(crate) fn flows_on_event(&mut self, ev: &Event) {
        let ids: Vec<String> = self.runs.keys().cloned().collect();
        let tag = ev.tag();
        for id in ids {
            let Some(t) = self
                .runs
                .get(&id)
                .and_then(|r| r.current())
                .and_then(|s| s.transitions.iter().find(|t| t.on.as_ref().is_some_and(|f| matches(f, ev))).cloned())
            else {
                continue;
            };
            self.flow_fire(&id, t, &tag, 0);
        }
    }

    pub(crate) fn flows_tick(&mut self, now: i64) {
        let ids: Vec<String> = self.runs.keys().cloned().collect();
        for id in ids {
            let Some(run) = self.runs.get(&id) else { continue };
            if run.flow.timeout_s.is_some_and(|to| (now - run.started_ns) as f64 >= to * 1e9) {
                self.flow_stop(&id, "timeout");
                continue;
            }
            let elapsed = (now - run.entered_ns) as f64 / 1e9;
            let best = run.current().and_then(|s| {
                s.transitions
                    .iter()
                    .filter(|t| t.on.is_none() && t.after_s.is_some_and(|a| elapsed >= a))
                    .min_by(|a, b| a.after_s.partial_cmp(&b.after_s).unwrap_or(std::cmp::Ordering::Equal))
                    .cloned()
            });
            if let Some(t) = best {
                self.flow_fire(&id, t, "after_s", 0);
            }
        }
    }
}
