// Open Scholarships query API — Netlify Edge Function (Deno).
//
// A thin, read-only filter layer over the static /scholarships.json bundle (itself CDN-cached).
// CORS-open so any site can call it. Mirrors the FastAPI filter semantics in api/app/loader.py.
import type { Config, Context } from "https://edge.netlify.com";

let cache: { at: number; data: any } | null = null;
const TTL = 60_000;

// Usage analytics into the same self-hosted Umami site Mission Control reads. The website id is
// public (it ships in the browser tracking script too), so it's fine inline. Events are fired
// non-blocking via context.waitUntil so they never slow or break the API response.
const UMAMI = "https://analytics.grudged.io";
const UMAMI_WEBSITE = "592dc95d-e6da-4432-ad7c-06fb06636ab0";

async function bundle(origin: string): Promise<any> {
  if (cache && Date.now() - cache.at < TTL) return cache.data;
  const res = await fetch(new URL("/scholarships.json", origin));
  const data = await res.json();
  cache = { at: Date.now(), data };
  return data;
}

function matches(rec: any, q: URLSearchParams): boolean {
  const elig = rec.eligibility ?? {};
  const geo = rec.geo ?? {};
  const levels: string[] = elig.education_level ?? [];

  const state = q.get("state");
  if (state) {
    const res = (elig.residency ?? []).map((r: string) => r.toUpperCase());
    if ((geo.state ?? "").toUpperCase() !== state.toUpperCase() && !res.includes(state.toUpperCase())) return false;
  }
  const level = q.get("level");
  if (level && !levels.includes(level) && !levels.includes("any")) return false;

  const field = q.get("field");
  if (field) {
    const fields = (elig.fields_of_study ?? []).map((f: string) => f.toLowerCase());
    if (fields.length && !fields.includes(field.toLowerCase())) return false; // empty = any field
  }
  const basis = q.get("basis");
  if (basis && (rec.award?.basis ?? null) !== basis) return false;

  const type = q.get("type");
  if (type && rec.type !== type) return false;

  const sponsorType = q.get("sponsor_type");
  if (sponsorType && rec.sponsor_type !== sponsorType) return false;

  const amountMin = q.get("amount_min");
  if (amountMin) {
    const amax = rec.award?.amount_max;
    if (amax == null || amax < Number(amountMin)) return false;
  }
  const after = q.get("deadline_after");
  if (after) {
    const d = rec.deadline?.date;
    if (d && d < after) return false; // undated (rolling) records always pass
  }
  const text = q.get("q");
  if (text) {
    const hay = `${rec.name} ${rec.summary ?? ""} ${rec.sponsor}`.toLowerCase();
    if (!hay.includes(text.toLowerCase())) return false;
  }
  return true;
}

export default async (request: Request, context: Context): Promise<Response> => {
  const url = new URL(request.url);
  const q = url.searchParams;
  const data = await bundle(url.origin);

  let results = (data.results ?? []).filter((r: any) => matches(r, q));
  const total = results.length;
  const limit = Math.min(Math.max(Number(q.get("limit") ?? 50), 1), 500);
  const offset = Math.max(Number(q.get("offset") ?? 0), 0);
  results = results.slice(offset, offset + limit);

  // Record the API call in Umami (non-blocking). Programmatic hits have no browser, so this is the
  // only way they show up alongside docs page views. Forward the caller IP so visitors aren't all
  // collapsed into the edge node.
  context.waitUntil(
    fetch(`${UMAMI}/api/send`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "user-agent": request.headers.get("user-agent") ?? "open-scholarships-edge",
        "x-forwarded-for": context.ip ?? request.headers.get("x-nf-client-connection-ip") ?? "",
      },
      body: JSON.stringify({
        type: "event",
        payload: {
          website: UMAMI_WEBSITE,
          hostname: "scholarships.grudged.io",
          url: url.pathname + url.search,
          name: "api_query",
          data: { state: q.get("state"), level: q.get("level"), q: q.get("q"), results: total },
        },
      }),
    }).catch(() => {}),
  );

  const body = {
    total,
    limit,
    offset,
    license: data.meta?.license ?? "CC-BY-4.0",
    attribution: data.meta?.attribution_required,
    results,
  };
  return new Response(JSON.stringify(body, null, 2), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "cache-control": "public, max-age=120, stale-while-revalidate=600",
      "x-license": "CC-BY-4.0",
    },
  });
};

export const config: Config = { path: "/api/scholarships" };
