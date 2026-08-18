// agent-away-message:managed-extension
// Lifecycle facts are ordered locally; source text is ephemeral daemon-only context.
import { spawn } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

type EventName =
  | "session_start"
  | "input"
  | "agent_start"
  | "tool_call"
  | "agent_end"
  | "session_shutdown";

const HOOK_TIMEOUT_MS = 1_000;
const SHUTDOWN_DRAIN_MS = 1_500;
const BRIDGE_EXECUTABLE = __AAM_EXECUTABLE__;
const COMMON_ARGS: string[] = __AAM_COMMON_ARGS__;
const CONTEXT_MODE: "generic" | "candid" = __AAM_CONTEXT_MODE__;
const MAX_CANDID_CONTEXT_CHARS = 6_000;
let chain = Promise.resolve();
let activeChild: ReturnType<typeof spawn> | undefined;
let activeEvent: EventName | undefined;
let activeAgentEndGeneration: number | undefined;
let closing = false;
let currentInput = "";
let lastAgentEnd = Promise.resolve();
let latestAgentEndGeneration = 0;
let pendingAgentEndContext: string | undefined;
let hasPendingAgentEnd = false;

function textFromContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => {
      if (typeof part === "string") return part;
      if (part && typeof part === "object" && "text" in part) {
        return typeof part.text === "string" ? part.text : "";
      }
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

function candidContext(messages: unknown): string | undefined {
  const assistant = Array.isArray(messages)
    ? messages
        .map((message) =>
          message &&
          typeof message === "object" &&
          "role" in message &&
          message.role === "assistant" &&
          "content" in message
            ? textFromContent(message.content)
            : "",
        )
        .filter(Boolean)
        .join("\n")
    : "";
  const context = [
    currentInput ? `User: ${currentInput}` : "",
    assistant ? `Assistant: ${assistant}` : "",
  ]
    .filter(Boolean)
    .join("\n");
  if (!context) return undefined;
  return context.slice(-MAX_CANDID_CONTEXT_CHARS);
}

function runHook(
  sessionId: string,
  event: EventName,
  text?: string,
  agentEndGeneration?: number,
): Promise<void> {
  return new Promise((resolve) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout>;
    const finish = (): void => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        activeChild = undefined;
        activeEvent = undefined;
        activeAgentEndGeneration = undefined;
        resolve();
      }
    };
    try {
      const command = [
        ...COMMON_ARGS,
        ...((event === "input" && CONTEXT_MODE === "candid") ||
        (event === "agent_end" && CONTEXT_MODE === "candid")
          ? ["--context-mode", CONTEXT_MODE, "pi-hook"]
          : ["pi-hook"]),
      ];
      const child = spawn(BRIDGE_EXECUTABLE, command, {
        stdio: ["pipe", "ignore", "ignore"],
      });
      activeChild = child;
      activeEvent = event;
      activeAgentEndGeneration = agentEndGeneration;
      timer = setTimeout(() => {
        child.kill();
        finish();
      }, HOOK_TIMEOUT_MS);
      child.once("error", finish);
      child.once("close", finish);
      child.stdin.end(
        JSON.stringify({
          event,
          session_id: sessionId,
          ...(text
            ? event === "agent_end"
              ? { candid_context: text }
              : { text }
            : {}),
        }),
      );
    } catch {
      // Presence must never interfere with Pi.
      resolve();
    }
  });
}

function emit(
  sessionId: string,
  event: EventName,
  text?: string,
  agentEndGeneration?: number,
): Promise<void> {
  chain = chain
    .then(() => {
      if (closing && event === "agent_end") {
        return agentEndGeneration === latestAgentEndGeneration
          ? runHook(sessionId, event, text, agentEndGeneration)
          : undefined;
      }
      return closing && event !== "session_shutdown"
        ? undefined
        : runHook(sessionId, event, text, agentEndGeneration);
    })
    .catch(() => undefined);
  return chain;
}

function drainTail(tail: Promise<void>): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, SHUTDOWN_DRAIN_MS);
    void tail.finally(() => {
      clearTimeout(timer);
      resolve();
    });
  });
}

export default function awayMessage(pi: ExtensionAPI): void {
  pi.on("session_start", (_event, ctx) => {
    closing = false;
    currentInput = "";
    pendingAgentEndContext = undefined;
    hasPendingAgentEnd = false;
    void emit(ctx.sessionManager.getSessionId(), "session_start");
  });
  pi.on("input", (event, ctx) => {
    if (event.source !== "extension") {
      currentInput = event.text;
      void emit(
        ctx.sessionManager.getSessionId(),
        "input",
        CONTEXT_MODE === "candid" ? event.text : undefined,
      );
    }
  });
  pi.on("agent_start", (_event, ctx) => {
    void emit(ctx.sessionManager.getSessionId(), "agent_start");
  });
  pi.on("tool_call", (_event, ctx) => {
    void emit(ctx.sessionManager.getSessionId(), "tool_call");
  });
  pi.on("agent_end", (event) => {
    pendingAgentEndContext =
      CONTEXT_MODE === "candid" ? candidContext(event.messages) : undefined;
    hasPendingAgentEnd = true;
  });
  pi.on("agent_settled", (_event, ctx) => {
    if (!hasPendingAgentEnd) return;
    hasPendingAgentEnd = false;
    latestAgentEndGeneration += 1;
    lastAgentEnd = emit(
      ctx.sessionManager.getSessionId(),
      "agent_end",
      pendingAgentEndContext,
      latestAgentEndGeneration,
    );
    pendingAgentEndContext = undefined;
  });
  pi.on("session_shutdown", async (_event, ctx) => {
    closing = true;
    if (
      activeEvent !== "agent_end" ||
      activeAgentEndGeneration !== latestAgentEndGeneration
    ) {
      activeChild?.kill();
    }
    await drainTail(lastAgentEnd);
    activeChild?.kill();
    await drainTail(
      runHook(ctx.sessionManager.getSessionId(), "session_shutdown"),
    );
  });
}
