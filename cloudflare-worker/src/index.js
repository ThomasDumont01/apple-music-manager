export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // GET /list — list all log files
    if (request.method === "GET" && url.pathname === "/list") {
      const listed = await env.LOGS_BUCKET.list({ prefix: "logs/" });
      const keys = listed.objects.map(obj => obj.key);
      return new Response(JSON.stringify(keys), {
        headers: { "Content-Type": "application/json" },
      });
    }

    // GET /download?key=... — download a specific file
    if (request.method === "GET" && url.pathname === "/download") {
      const key = url.searchParams.get("key");
      if (!key) return new Response("Missing key", { status: 400 });
      const obj = await env.LOGS_BUCKET.get(key);
      if (!obj) return new Response("Not found", { status: 404 });
      return new Response(obj.body, {
        headers: { "Content-Type": "application/jsonl" },
      });
    }

    // POST /upload — receive logs
    if (request.method === "POST") {
      const ip = request.headers.get("CF-Connecting-IP") || "unknown";
      const body = await request.text();
      if (!body || body.length < 10) {
        return new Response("Empty logs", { status: 400 });
      }

      const now = new Date().toISOString().slice(0, 10);
      const userId = request.headers.get("X-User-Id") || "anonymous";
      const key = `logs/${now}_${userId}.jsonl`;

      await env.LOGS_BUCKET.put(key, body, {
        customMetadata: {
          uploadedAt: new Date().toISOString(),
          size: String(body.length),
          ip: ip,
        },
      });

      return new Response(JSON.stringify({ ok: true, key }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    return new Response("Method not allowed", { status: 405 });
  },
};