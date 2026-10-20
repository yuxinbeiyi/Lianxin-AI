export type WorkspaceId =
  | "chat"
  | "music"
  | "time-capsule"
  | "prism-memory"
  | "study-room";

export type ActivityState = "idle" | "thinking" | "speaking" | "working";

export type ExecutionCardData = {
  taskId?: string;
  threadId?: string;
  status?: string;
  goal?: string;
  rounds?: number;
  elapsed?: number;
  reason?: string;
  summary?: string;
  narrated?: boolean;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  time: string;
  kind?: "text" | "task" | "result" | "file" | "interaction" | "execution_task";
  execution?: ExecutionCardData;
  imageUrl?: string;
  imageName?: string;
  imageStatus?: "pending" | "success" | "error";
  imageDescription?: string;
  fileName?: string;
  fileSize?: number;
  attachments?: Array<{ kind: "image" | "file"; fileName: string; path?: string }>;
};
