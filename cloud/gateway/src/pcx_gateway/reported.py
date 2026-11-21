"""Apply only node-reported events, never desired ToolCalls, to the physical mirror."""

import msgspec
from pcx_contracts.commands import Clear, Project, Remove, SetTransform, Update
from pcx_contracts.events import (
    DeviceChanged,
    FlowStateChanged,
    ObjectAppeared,
    ObjectMoved,
    ObjectRemoved,
    TouchDown,
    TouchMove,
    TouchUp,
)
from pcx_contracts.widgets import Widget
from pcx_contracts.world import WorldState


def upsert(items, item):
    return [old for old in items if old.id != item.id] + [item]


def apply_reported(world: WorldState, payload, t_pub_ns):
    # Events have no world revision: keep the authoritative snapshot's rev unchanged.
    if isinstance(payload, ObjectAppeared):
        world.objects = upsert(world.objects, payload.object)
    elif isinstance(payload, ObjectRemoved):
        world.objects = [obj for obj in world.objects if obj.id != payload.object_id]
    elif isinstance(payload, ObjectMoved):
        for obj in world.objects:
            if obj.id == payload.object_id:
                dx = payload.center_mm.x - obj.center_mm.x
                dy = payload.center_mm.y - obj.center_mm.y
                if payload.polygon_mm is not None:
                    obj.polygon_mm = payload.polygon_mm
                    # There is no reported bbox with the new polygon; avoid exposing a stale one.
                    obj.bbox_mm = None
                else:
                    obj.polygon_mm = [type(point)(point.x + dx, point.y + dy) for point in obj.polygon_mm]
                    if obj.bbox_mm:
                        obj.bbox_mm = type(obj.bbox_mm)(
                            obj.bbox_mm.x + dx, obj.bbox_mm.y + dy, obj.bbox_mm.w, obj.bbox_mm.h
                        )
                obj.center_mm = payload.center_mm
                obj.last_seen = t_pub_ns
    elif isinstance(payload, DeviceChanged):
        world.devices = upsert(world.devices, payload.device)
    elif isinstance(payload, (TouchDown, TouchMove)):
        world.touches = upsert(world.touches, payload.touch)
    elif isinstance(payload, TouchUp):
        world.touches = [touch for touch in world.touches if touch.id != payload.touch.id]
    elif isinstance(payload, FlowStateChanged):
        world.active_flows = [flow for flow in world.active_flows if flow != payload.flow_id]
        if payload.to_state is not None:
            world.active_flows.append(payload.flow_id)
    elif isinstance(payload, Project):
        for widget in payload.widgets:
            world.widgets = upsert(world.widgets, widget)
    elif isinstance(payload, Remove):
        world.widgets = [widget for widget in world.widgets if widget.id not in payload.ids]
    elif isinstance(payload, Clear):
        world.widgets = []
    elif isinstance(payload, Update):
        world.widgets = [
            msgspec.convert({**msgspec.to_builtins(widget), **payload.set}, type=Widget)
            if widget.id == payload.id
            else widget
            for widget in world.widgets
        ]
    elif isinstance(payload, SetTransform):
        transform = payload.transform
        world.transforms = [
            old
            for old in world.transforms
            if (old.from_frame, old.to_frame) != (transform.from_frame, transform.to_frame)
        ]
        world.transforms.append(transform)
