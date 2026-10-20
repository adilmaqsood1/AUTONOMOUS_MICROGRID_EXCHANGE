const API_BASE_URL = '';

class ApiError extends Error {
  status: number;
  data: any;

  constructor(status: number, message: string, data?: any) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

export async function apiRequest<T = any>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const token = localStorage.getItem('mgx_token');
  const headers = new Headers(options.headers || {});

  if (!headers.has('Content-Type') && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const url = `${API_BASE_URL}${endpoint}`;
  try {
    const res = await fetch(url, { ...options, headers });
    if (res.status === 401) {
      localStorage.removeItem('mgx_token');
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login';
      }
    }

    if (!res.ok) {
      let errBody: any;
      try {
        errBody = await res.json();
      } catch {
        errBody = await res.text();
      }
      throw new ApiError(
        res.status,
        errBody?.detail || errBody?.message || `HTTP ${res.status} Error`,
        errBody
      );
    }

    if (res.status === 204) {
      return {} as T;
    }

    return await res.json();
  } catch (error: any) {
    if (error instanceof ApiError) {
      throw error;
    }
    throw new ApiError(0, error.message || 'Network error connecting to microgrid backend');
  }
}
