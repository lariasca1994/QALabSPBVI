export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiResponse<T> {
  status: number;
  data: T;
}

async function requestWithMetadata<T>(
  path: string,
  init: RequestInit = {},
): Promise<ApiResponse<T>> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`/api${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  if (!response.ok) {
    let message = `La solicitud falló (${response.status}).`;
    try {
      const body: unknown = await response.json();
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof body.detail === "string"
      ) {
        message = body.detail;
      } else if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        Array.isArray(body.detail)
      ) {
        // Errores de validación 422: se muestran los mensajes legibles de cada campo.
        message = body.detail
          .map((item: { msg?: string }) => (item.msg ?? "").replace(/^Value error, /, ""))
          .filter(Boolean)
          .join(" ");
      }
    } catch {
      message = `El servidor respondió con el estado ${response.status}.`;
    }
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) {
    return { status: response.status, data: undefined as T };
  }
  return { status: response.status, data: (await response.json()) as T };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  return (await requestWithMetadata<T>(path, init)).data;
}

async function csrfToken(): Promise<string> {
  const result = await request<{ csrf_token: string }>("/auth/csrf");
  return result.csrf_token;
}

export interface User {
  id: number;
  email: string;
  display_name: string;
  role: "admin" | "administrador" | "usuario";
  is_active: boolean;
  notification_status?: "sent" | "failed" | null;
}

export interface EpicMember {
  user_id: number;
  email?: string;
  display_name?: string;
}

export interface Epic {
  key: string;
  title: string;
  description?: string;
  members: EpicMember[];
  created_at_epoch?: number;
}

/** Contrato REST/JSON ejecutable de un CP: lo que se envía y lo que se espera recibir. */
export interface CaseDefinition {
  request_method: string;
  request_path: string;
  request_query: Record<string, unknown>;
  request_headers: Record<string, string>;
  request_body: unknown;
  expected_status_codes: number[];
  expected_response: unknown;
}

export interface CaseVersion {
  version: number;
  request: { method: string; path: string; query?: unknown; headers?: unknown; body?: unknown };
  expected_status_codes: number[];
  expected_response?: unknown;
  replaced_by?: { email?: string; display_name?: string };
  replaced_at_epoch?: number;
  change_note?: string;
}

export interface ImportSummary {
  epic_key: string;
  program: string;
  created: { stories: number; test_cases: number; tasks: number };
  skipped: { stories: number; test_cases: number; tasks: number };
  updated_test_cases?: number;
  notification_status: string;
}

/** Llave creada por un CP de la épica; las transacciones la eligen según su tipo. */
export interface EpicKey {
  key_type: string;
  key_value: string;
  spbvi_id: string;
  status: string;
  deposit_product_id?: string;
  case_key?: string;
  created_at_epoch?: number;
}

export interface WorkItem {
  key: string;
  kind: "story" | "test_case" | "task" | string;
  title: string;
  description?: string;
  priority?: string;
  status?: string;
  story_key?: string;
  acceptance_criteria?: string[];
  preconditions?: string[];
  steps?: Array<{ action: string; expected: string }>;
  expected_result?: string;
  assignee?: EpicMember | null;
  version?: number;
  versions?: CaseVersion[];
  expected_status_codes?: number[];
  expected_response?: unknown;
  created_by?: { email?: string; display_name?: string };
  request?: {
    method?: string;
    path?: string;
    query?: Record<string, unknown>;
    headers?: Record<string, string>;
    body?: unknown;
  };
}

export interface Execution {
  key: string;
  passed: boolean;
  created_at_epoch?: number;
  actor?: { email?: string; display_name?: string };
  request?: { method?: string; url?: string; body?: unknown };
  result?: { status_code?: number; duration_ms?: number; body?: unknown };
  placeholders?: Record<string, string>;
}

export interface PaymentKey {
  id: number;
  key_type: string;
  key_value: string;
  spbvi_id: string;
  deposit_product_id: string;
  status: string;
}

export interface Account {
  id: string;
  spbvi_id: string;
  balance_cents: number;
}

export interface Payment {
  id: number;
  operation_id: string;
  source_account_id: string;
  destination_account_id: string;
  amount_cents: number;
  payment_type: string;
  status: string;
  replayed: boolean;
  pacs008_xml?: string;
  pacs002_xml?: string;
}

export interface CsrfProtectedRequest {
  method: string;
  path: string;
  body?: unknown;
}

export type KeyTypeInfo = {
  code: string;
  label: string;
  example: string;
  hint: string;
};

async function mutate<T>(path: string, method: "POST" | "PUT", body: unknown): Promise<T> {
  const token = await csrfToken();
  return request<T>(path, {
    method,
    headers: { "X-CSRF-Token": token },
    body: JSON.stringify(body),
  });
}

export const api = {
  keyTypes(): Promise<KeyTypeInfo[]> {
    return request<KeyTypeInfo[]>("/keys/types");
  },
  async me(): Promise<User> {
    return request<User>("/auth/me");
  },
  async login(email: string, password: string): Promise<void> {
    const token = await csrfToken();
    await request("/auth/login", {
      method: "POST",
      headers: { "X-CSRF-Token": token },
      body: JSON.stringify({ email, password }),
    });
  },
  async verifyCode(email: string, code: string): Promise<void> {
    const token = await csrfToken();
    await request("/auth/verify-email-code", {
      method: "POST",
      headers: { "X-CSRF-Token": token },
      body: JSON.stringify({ email, code }),
    });
  },
  async resendCode(): Promise<void> {
    const token = await csrfToken();
    await request("/auth/resend-code", {
      method: "POST",
      headers: { "X-CSRF-Token": token },
    });
  },
  async logout(): Promise<void> {
    const token = await csrfToken();
    await request("/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": token },
    });
  },
  health(): Promise<{ status: string }> {
    return request("/health");
  },
  epics(): Promise<Epic[]> {
    return request("/qa/epics");
  },
  workItems(epicKey: string): Promise<WorkItem[]> {
    return request(`/qa/epics/${encodeURIComponent(epicKey)}/work-items`);
  },
  async executeCase(caseKey: string, selectedKeys: Record<string, string> = {}): Promise<Execution> {
    return mutate<Execution>(`/qa/cases/${encodeURIComponent(caseKey)}/execute`, "POST", { selected_keys: selectedKeys });
  },
  epicKeys(epicKey: string): Promise<EpicKey[]> {
    return request(`/qa/epics/${encodeURIComponent(epicKey)}/keys`);
  },
  users(): Promise<User[]> {
    return request("/auth/users");
  },
  async createEpic(payload: { title: string; description: string; member_ids: number[] }): Promise<Epic> {
    return mutate<Epic>("/qa/epics", "POST", payload);
  },
  async updateEpicMembers(epicKey: string, memberIds: number[]): Promise<Epic> {
    return mutate<Epic>(`/qa/epics/${encodeURIComponent(epicKey)}/members`, "PUT", { member_ids: memberIds });
  },
  async createStory(epicKey: string, payload: {
    title: string;
    description: string;
    priority: string;
    acceptance_criteria: string[];
  }): Promise<WorkItem> {
    return mutate<WorkItem>(`/qa/epics/${encodeURIComponent(epicKey)}/stories`, "POST", payload);
  },
  async createTestCase(storyKey: string, payload: CaseDefinition & {
    title: string;
    description: string;
    priority: string;
    preconditions: string[];
    steps: Array<{ action: string; expected: string }>;
    expected_result: string;
  }): Promise<WorkItem> {
    return mutate<WorkItem>(`/qa/stories/${encodeURIComponent(storyKey)}/test-cases`, "POST", payload);
  },
  async updateCaseDefinition(caseKey: string, payload: CaseDefinition & { change_note: string }): Promise<WorkItem> {
    return mutate<WorkItem>(`/qa/cases/${encodeURIComponent(caseKey)}`, "PUT", payload);
  },
  async createTask(epicKey: string, payload: { title: string; description: string; assignee_id: number | null }): Promise<WorkItem> {
    return mutate<WorkItem>(`/qa/epics/${encodeURIComponent(epicKey)}/tasks`, "POST", payload);
  },
  async assignTask(taskKey: string, assigneeId: number): Promise<WorkItem> {
    return mutate<WorkItem>(`/qa/tasks/${encodeURIComponent(taskKey)}/assign`, "POST", { assignee_id: assigneeId });
  },
  async transitionTask(taskKey: string, status: string): Promise<WorkItem> {
    return mutate<WorkItem>(`/qa/tasks/${encodeURIComponent(taskKey)}/transition`, "POST", { status });
  },
  async importProgram(epicKey: string, program: unknown, updateExisting = false): Promise<ImportSummary> {
    const query = updateExisting ? "?update_existing=true" : "";
    return mutate<ImportSummary>(`/qa/epics/${encodeURIComponent(epicKey)}/import${query}`, "POST", program);
  },
  caseExecutions(caseKey: string): Promise<Execution[]> {
    return request(`/qa/cases/${encodeURIComponent(caseKey)}/executions`);
  },
  async createUser(payload: {
    email: string;
    display_name: string;
    password: string;
    role: "administrador" | "usuario";
  }): Promise<User> {
    const token = await csrfToken();
    return request("/auth/users", {
      method: "POST",
      headers: { "X-CSRF-Token": token },
      body: JSON.stringify(payload),
    });
  },
  async createAccount(payload: {
    account_id: string;
    spbvi_id: string;
    balance_cents: number;
  }): Promise<ApiResponse<Account>> {
    const token = await csrfToken();
    return requestWithMetadata("/accounts", {
      method: "POST",
      headers: { "X-CSRF-Token": token },
      body: JSON.stringify(payload),
    });
  },
  async registerKey(
    spbviId: string,
    payload: {
      key_type: string;
      key_value: string;
      deposit_product_id: string;
      owner_email?: string;
    },
  ): Promise<ApiResponse<PaymentKey>> {
    const token = await csrfToken();
    return requestWithMetadata(`/difes/${encodeURIComponent(spbviId)}/keys`, {
      method: "POST",
      headers: { "X-CSRF-Token": token },
      body: JSON.stringify(payload),
    });
  },
  async resolveKey(
    spbviId: string,
    keyType: string,
    keyValue: string,
  ): Promise<ApiResponse<PaymentKey>> {
    const query = new URLSearchParams({ key_type: keyType, key_value: keyValue });
    return requestWithMetadata(
      `/difes/${encodeURIComponent(spbviId)}/keys/resolve?${query.toString()}`,
    );
  },
  async updateKeyLifecycle(
    spbviId: string,
    action: "suspend" | "reactivate" | "owner" | "delete",
    payload: Record<string, string>,
  ): Promise<ApiResponse<PaymentKey | { deleted: boolean; key_type: string; key_value: string; spbvi_id: string }>> {
    const token = await csrfToken();
    const method = action === "owner" ? "PATCH" : action === "delete" ? "DELETE" : "POST";
    const endpoint = action === "owner" ? "owner" : action;
    return requestWithMetadata(
      `/difes/${encodeURIComponent(spbviId)}/keys${endpoint === "delete" ? "" : `/${endpoint}`}`,
      {
        method,
        headers: { "X-CSRF-Token": token },
        body: JSON.stringify(payload),
      },
    );
  },
  async createPayment(
    paymentType: "intra" | "inter",
    payload: {
      operation_id: string;
      source_account_id: string;
      destination_key_type: string;
      destination_key_value: string;
      amount_cents: number;
    },
  ): Promise<ApiResponse<Payment | { detail: string; operation_id: string; status: string; pacs002_xml?: string }>> {
    const token = await csrfToken();
    const path = paymentType === "inter" ? "/payments/inter-spbvi" : "/payments";
    return requestWithMetadata(path, {
      method: "POST",
      headers: { "X-CSRF-Token": token },
      body: JSON.stringify(payload),
    });
  },
};
