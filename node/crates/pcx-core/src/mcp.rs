//! Servidor MCP (SDK oficial `rmcp`) con las tools de contratos tal cual (esquemas planos de `tools.json`).
//! Dos modos: `Local` (tools sobre el actor de este proceso) y `Proxy` (stdio → daemon por Streamable HTTP, para que
//! todos los agentes compartan la misma mesa).

use std::sync::Arc;

use base64::Engine;
use rmcp::model::{
    CallToolRequestParams, CallToolResponse, CallToolResult, ContentBlock, Implementation, ListToolsResult, PaginatedRequestParams, ServerCapabilities,
    ServerConfig, Tool,
};
use rmcp::service::{RequestContext, RunningService};
use rmcp::{ErrorData, RoleClient, RoleServer, ServerHandler};

use crate::tools::{ToolOutput, Tools, INSTRUCTIONS, TOOLS_JSON};

pub fn tool_list() -> Vec<Tool> {
    serde_json::from_str(TOOLS_JSON).expect("contracts/schema/tools.json inválido")
}

pub fn to_mcp(out: ToolOutput) -> CallToolResult {
    let mut content = vec![];
    if let Some((img, mime)) = &out.image {
        content.push(ContentBlock::image(base64::engine::general_purpose::STANDARD.encode(img), mime.clone()));
    }
    content.push(ContentBlock::text(out.text));
    if out.is_error {
        CallToolResult::error(content)
    } else {
        CallToolResult::success(content)
    }
}

#[derive(Clone)]
enum Backend {
    Local(Tools),
    Proxy(Arc<RunningService<RoleClient, ()>>),
}

#[derive(Clone)]
pub struct PcxMcp {
    backend: Backend,
    tools: Arc<Vec<Tool>>,
}

impl PcxMcp {
    pub fn local(tools: Tools) -> Self {
        PcxMcp { backend: Backend::Local(tools), tools: Arc::new(tool_list()) }
    }

    pub fn proxy(client: RunningService<RoleClient, ()>) -> Self {
        PcxMcp { backend: Backend::Proxy(Arc::new(client)), tools: Arc::new(tool_list()) }
    }
}

impl ServerHandler for PcxMcp {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new("pcx", env!("CARGO_PKG_VERSION")))
            .with_instructions(INSTRUCTIONS)
    }

    async fn list_tools(&self, _request: Option<PaginatedRequestParams>, _context: RequestContext<RoleServer>) -> Result<ListToolsResult, ErrorData> {
        Ok(ListToolsResult::with_all_items((*self.tools).clone()))
    }

    async fn call_tool(&self, request: CallToolRequestParams, _context: RequestContext<RoleServer>) -> Result<CallToolResponse, ErrorData> {
        match &self.backend {
            Backend::Local(t) => {
                let args = request.arguments.map(serde_json::Value::Object).unwrap_or_default();
                Ok(to_mcp(t.call(&request.name, args, "mcp", None).await).into())
            }
            Backend::Proxy(c) => match c.call_tool(request).await {
                Ok(r) => Ok(r.into()),
                Err(e) => Ok(CallToolResult::error(vec![ContentBlock::text(format!("daemon pcx: {e}"))]).into()),
            },
        }
    }
}
