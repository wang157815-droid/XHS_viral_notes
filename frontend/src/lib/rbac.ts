export type Role = "admin" | "analyst" | "viewer";

export type PermissionAction =
  | "task.read_own"
  | "task.write_own"
  | "task.read_all"
  | "conversation.read_own"
  | "conversation.write_own"
  | "conversation.read_all"
  | "knowledge.read"
  | "knowledge.write"
  | "knowledge.delete"
  | "settings.system.read"
  | "settings.system.write"
  | "settings.users.manage"
  | "settings.observability.read"
  | "settings.maintenance.write"
  | "xhs_credential.manage_self";

const ROLE_LEVEL: Record<Role, number> = {
  viewer: 1,
  analyst: 2,
  admin: 3,
};

const ACTION_MIN_ROLE: Record<PermissionAction, Role> = {
  "task.read_own": "viewer",
  "task.write_own": "analyst",
  "task.read_all": "admin",
  "conversation.read_own": "viewer",
  "conversation.write_own": "analyst",
  "conversation.read_all": "admin",
  "knowledge.read": "viewer",
  "knowledge.write": "analyst",
  "knowledge.delete": "admin",
  "settings.system.read": "viewer",
  "settings.system.write": "admin",
  "settings.users.manage": "admin",
  "settings.observability.read": "admin",
  "settings.maintenance.write": "admin",
  "xhs_credential.manage_self": "analyst",
};

export function normalizeRole(role?: string | null): Role {
  if (role === "admin" || role === "analyst" || role === "viewer") return role;
  if (role === "user") return "analyst";
  return "viewer";
}

export function canRole(role: string | null | undefined, action: PermissionAction): boolean {
  const normalized = normalizeRole(role);
  const required = ACTION_MIN_ROLE[action];
  return ROLE_LEVEL[normalized] >= ROLE_LEVEL[required];
}

export function roleLabel(role?: string | null): string {
  switch (normalizeRole(role)) {
    case "admin":
      return "管理员";
    case "analyst":
      return "分析师";
    case "viewer":
      return "只读成员";
  }
}
