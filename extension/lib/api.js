// Thin client for the LeadHarvest /api endpoints. Auth is the user's API token
// sent as a Bearer header.

export class Api {
  constructor(baseUrl, token) {
    this.base = (baseUrl || "").replace(/\/+$/, "");
    this.token = (token || "").trim();
  }

  async _req(path, { method = "GET", body } = {}) {
    const resp = await fetch(this.base + path, {
      method,
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + this.token,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!resp.ok) {
      let detail = "";
      try {
        detail = (await resp.text()).slice(0, 300);
      } catch {
        /* ignore */
      }
      throw new Error(`HTTP ${resp.status} on ${path} ${detail}`);
    }
    if (resp.status === 204) return null;
    return resp.json();
  }

  me() {
    return this._req("/api/me");
  }

  claim() {
    return this._req("/api/jobs/claim", { method: "POST", body: {} });
  }

  progress(jobId, payload) {
    return this._req(`/api/jobs/${jobId}/progress`, { method: "POST", body: payload });
  }

  postLeads(jobId, leads) {
    return this._req(`/api/jobs/${jobId}/leads`, { method: "POST", body: { leads } });
  }

  complete(jobId, payload) {
    return this._req(`/api/jobs/${jobId}/complete`, { method: "POST", body: payload });
  }

  fail(jobId, error) {
    return this._req(`/api/jobs/${jobId}/fail`, {
      method: "POST",
      body: { error: String(error).slice(0, 2000) },
    });
  }
}
