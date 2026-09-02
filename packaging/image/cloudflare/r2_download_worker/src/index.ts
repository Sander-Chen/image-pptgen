const SECURITY_HEADERS = {
  "Cache-Control": "private, no-store",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex, nofollow, noarchive",
} as const;

function response(status: number, body: string, extraHeaders: HeadersInit = {}): Response {
  return new Response(body, {
    status,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      ...SECURITY_HEADERS,
      ...extraHeaders,
    },
  });
}

function resolveObjectKey(
  pathname: string,
  downloadPrefix: string,
  r2Prefix: string,
): string | null {
  const routePrefix = `/${downloadPrefix}/`;
  if (!pathname.startsWith(routePrefix)) {
    return null;
  }

  const objectKey = pathname.slice(routePrefix.length);
  const requiredPrefix = `${r2Prefix}/`;
  if (!objectKey.startsWith(requiredPrefix) || objectKey.includes("\\")) {
    return null;
  }

  const segments = objectKey.split("/");
  if (segments.some((segment) => segment.length === 0 || segment === "." || segment === "..")) {
    return null;
  }
  return objectKey;
}

function objectHeaders(object: R2Object): Headers {
  const headers = new Headers(SECURITY_HEADERS);
  object.writeHttpMetadata(headers);
  headers.set("Content-Type", headers.get("Content-Type") ?? "application/octet-stream");
  headers.set("Content-Length", String(object.size));
  headers.set("ETag", object.httpEtag);
  return headers;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.search || url.hash) {
      return response(404, "Not found\n");
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return response(405, "Method not allowed\n", { Allow: "GET, HEAD" });
    }

    const objectKey = resolveObjectKey(url.pathname, env.DOWNLOAD_PREFIX, env.R2_PREFIX);
    if (objectKey === null) {
      return response(404, "Not found\n");
    }

    try {
      if (request.method === "HEAD") {
        const object = await env.ARCHIVE.head(objectKey);
        if (object === null) {
          return response(404, "Not found\n");
        }
        return new Response(null, { status: 200, headers: objectHeaders(object) });
      }

      const object = await env.ARCHIVE.get(objectKey);
      if (object === null) {
        return response(404, "Not found\n");
      }
      return new Response(object.body, { status: 200, headers: objectHeaders(object) });
    } catch (error) {
      console.error(
        JSON.stringify({
          message: "R2 download failed",
          objectKey,
          error: error instanceof Error ? error.message : String(error),
        }),
      );
      return response(500, "Download unavailable\n");
    }
  },
} satisfies ExportedHandler<Env>;
