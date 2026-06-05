import { HttpClient } from "./types.js";

export class HttpError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly body?: string
  ) {
    super(message);
    this.name = "HttpError";
  }
}

export type HttpOptions = {
  timeoutMs: number;
  retries: number;
  // When set, wait a random delay in [min, max] ms before each request.
  // Use for HTML-scraping providers (BambooHR, Workday, TeamTailor, Workable)
  // to reduce the risk of silent blocking. Leave unset for JSON API providers.
  jitterMs?: [min: number, max: number];
};

const USER_AGENTS = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) Gecko/20100101 Firefox/147.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
  "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
];

function randomUA(): string {
  return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)]!;
}

const transientStatuses = new Set([408, 425, 429, 500, 502, 503, 504]);

export function createHttpClient(options: HttpOptions): HttpClient {
  async function request(url: string, init: RequestInit = {}): Promise<Response> {
    let lastError: unknown;

    if (options.jitterMs) {
      const [min, max] = options.jitterMs;
      await delay(min + Math.random() * (max - min));
    }

    for (let attempt = 0; attempt <= options.retries; attempt += 1) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), options.timeoutMs);

      try {
        const response = await fetch(url, {
          ...init,
          signal: controller.signal,
          headers: {
            "User-Agent": randomUA(),
            ...init.headers
          }
        });

        if (!response.ok) {
          const body = await response.text().catch(() => "");
          const error = new HttpError(`HTTP ${response.status} for ${url}`, response.status, body);
          if (attempt < options.retries && transientStatuses.has(response.status)) {
            await delay(backoffMs(attempt));
            continue;
          }
          throw error;
        }

        return response;
      } catch (error) {
        lastError = error;
        if (error instanceof HttpError && !isTransientHttpError(error)) {
          throw error;
        }
        if (attempt >= options.retries) {
          throw normalizeError(error, url);
        }
        await delay(backoffMs(attempt));
      } finally {
        clearTimeout(timeout);
      }
    }

    throw normalizeError(lastError, url);
  }

  return {
    async getJson<T = unknown>(url: string, init?: RequestInit): Promise<T> {
      const response = await request(url, { ...init, method: "GET" });
      return response.json() as Promise<T>;
    },
    async postJson<T = unknown>(url: string, body: unknown, init?: RequestInit): Promise<T> {
      const response = await request(url, {
        ...init,
        method: "POST",
        body: JSON.stringify(body),
        headers: {
          "Content-Type": "application/json",
          ...init?.headers
        }
      });
      return response.json() as Promise<T>;
    },
    async getText(url: string, init?: RequestInit): Promise<string> {
      const response = await request(url, { ...init, method: "GET" });
      return response.text();
    }
  };
}

function isTransientHttpError(error: HttpError): boolean {
  return error.status !== undefined && transientStatuses.has(error.status);
}

function normalizeError(error: unknown, url: string): Error {
  if (error instanceof Error) {
    if (error.name === "AbortError") {
      return new Error(`Timeout while fetching ${url}`);
    }
    return error;
  }
  return new Error(`Unknown error while fetching ${url}`);
}

function backoffMs(attempt: number): number {
  // Exponential backoff with ±20% random jitter to avoid thundering herd
  const base = Math.min(5000, 250 * 2 ** attempt);
  return base + Math.random() * base * 0.4 - base * 0.2;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
