import { tmpdir } from "node:os";
import { query } from "@anthropic-ai/claude-agent-sdk";
import type { ModelId } from "../types.js";
import type { createGenieTools } from "./tools.js";

export type AgentRunInput = {
  model: ModelId;
  fallbackModel: ModelId | undefined;
  maxTurns: number;
  budgetUsd: number;
  systemPrompt: string;
  prompt: string;
  tools: ReturnType<typeof createGenieTools>;
  abortController: AbortController;
  /** Raw traveler message and the facts the router saw, for runners that don't use a model. */
  message: string;
};

export type AgentRunResult = {
  text: string;
  costUsd: number;
  turns: number;
  /** Why the run stopped before finishing, if it did: "step limit", "budget" or "aborted". */
  stoppedEarly: string | null;
  /** The model that actually answered, which differs from the requested one after an SDK fallback. */
  answeredBy: ModelId;
};

export interface AgentRunner {
  run(input: AgentRunInput, onText: (delta: string) => void): Promise<AgentRunResult>;
}

/** Runs one traveler turn on the Claude Agent SDK. */
export class AgentSdkRunner implements AgentRunner {
  async run(input: AgentRunInput, onText: (delta: string) => void): Promise<AgentRunResult> {
    let text = "";
    let answeredBy: ModelId = input.model;
    const q = query({
      prompt: input.prompt,
      options: {
        model: input.model,
        fallbackModel: input.fallbackModel,
        systemPrompt: input.systemPrompt,
        // No built-in Claude Code tools: Genie only gets its own MCP tools.
        tools: [],
        mcpServers: { genie: input.tools.server },
        allowedTools: input.tools.allowedTools,
        permissionMode: "dontAsk",
        // Don't read CLAUDE.md, settings or skills from disk, and don't write transcripts.
        settingSources: [],
        persistSession: false,
        cwd: tmpdir(),
        maxTurns: input.maxTurns,
        maxBudgetUsd: input.budgetUsd,
        effort: input.model === "claude-opus-5-5" ? "high" : "medium",
        includePartialMessages: true,
        abortController: input.abortController,
        env: { ...process.env, CLAUDE_AGENT_SDK_CLIENT_APP: "trip-genie/0.1.0" },
      },
    });

    for await (const msg of q) {
      if (msg.type === "stream_event" && msg.parent_tool_use_id === null) {
        const ev = msg.event;
        if (ev.type === "content_block_delta" && ev.delta.type === "text_delta") {
          text += ev.delta.text;
          onText(ev.delta.text);
        }
      } else if (msg.type === "assistant" && msg.parent_tool_use_id === null) {
        answeredBy = (msg.message.model as ModelId) ?? answeredBy;
      } else if (msg.type === "result") {
        const stoppedEarly =
          msg.subtype === "error_max_turns" ? "step limit" : msg.subtype === "error_max_budget_usd" ? "budget" : null;
        if (msg.subtype === "error_during_execution") {
          throw new Error(`Agent run failed: ${"errors" in msg ? JSON.stringify(msg.errors) : "unknown error"}`);
        }
        return { text, costUsd: msg.total_cost_usd, turns: msg.num_turns, stoppedEarly, answeredBy };
      }
    }
    return { text, costUsd: 0, turns: 0, stoppedEarly: input.abortController.signal.aborted ? "aborted" : null, answeredBy };
  }
}
