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
}

export interface Epic {
  key: string;
  title: string;
  description?: string;
  members: Array<{ user_id: number; email?: string }>;
  created_at_epoch?: number;
}

export interface WorkItem {
  key: string;
  kind: "story" | "test_case" | "task" | string;
  title: string;
  description?: string;
  priority?: string;
  status?: string;
  request?: {
    method?: string;
    path?: string;
  };
}

export interface Execution {
  key: string;
  passed: boolean;
  request?: { method?: string; url?: string };
  result?: { status_code?: number; duration_ms?: number; body?: unknown };
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

export const api = {
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
  async executeCase(caseKey: string): Promise<Execution> {
    const token = await csrfToken();
    return request(`/qa/cases/${encodeURIComponent(caseKey)}/execute`, {
      method: "POST",
      headers: { "X-CSRF-Token": token },
    });
  },
  users(): Promise<User[]> {
    return request("/auth/users");
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
