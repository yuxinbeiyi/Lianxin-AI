export type WorkspaceId =
  | "chat"
  | "music"
  | "time-capsule"
  | "prism-memory"
  | "study-room";

export type ActivityState = "idle" | "thinking" | "speaking" | "working";

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  time: string;
  kind?: "text" | "task" | "result" | "file" | "interaction";
  imageUrl?: string;
  imageName?: string;
  imageStatus?: "pending" | "success" | "error";
  imageDescription?: string;
  fileName?: string;
  fileSize?: number;
  attachments?: Array<{ kind: "image" | "file"; fileName: string; path?: string }>;
};
