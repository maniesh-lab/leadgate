/** The API client: URLs, the token header, and turning error answers into messages. */
import { describe, expect, it, vi } from "vitest";
import { getToken, setToken } from "../auth/token";
import { fakeApi, reply } from "../test/render";
import { ApiError, api, apiUrl, describeError, setUnauthorizedHandler } from "./client";

describe("apiUrl", () => {
  it("leaves out empty values", () => {
    expect(apiUrl("/api/leads", { status: "new", q: "", flag: null, page: 2, x: undefined })).toBe(
      "/api/leads?status=new&page=2",
    );
    expect(apiUrl("/api/runs")).toBe("/api/runs");
  });
});

describe("api", () => {
  it("sends the token and JSON", async () => {
    setToken("abc");
    const seen: RequestInit[] = [];
    fakeApi({
      "POST /api/campaigns": (_url, init) => {
        seen.push(init);
        return { id: 1 };
      },
    });

    expect(await api.post("/api/campaigns", { name: "x" })).toEqual({ id: 1 });
    expect(seen[0].headers).toEqual({
      Authorization: "Bearer abc",
      "Content-Type": "application/json",
    });
    expect(seen[0].body).toBe('{"name":"x"}');
  });

  it("logs out on a 401, unless another tab saved a new token meanwhile", async () => {
    const logout = vi.fn();
    setUnauthorizedHandler(logout);
    const expired = () => reply(401, { error: { code: "unauthorized", message: "expired" } });
    fakeApi({
      "GET /api/runs": expired,
      "GET /api/leads": () => {
        setToken("new-token"); // a password change in another tab, while this request ran
        return expired();
      },
    });

    setToken("old-token");
    await api.get("/api/leads").catch(() => {});
    expect(logout).not.toHaveBeenCalled(); // that would have wiped the other tab's new token
    expect(getToken()).toBe("new-token");

    await api.get("/api/runs").catch(() => {});
    expect(logout).toHaveBeenCalledOnce();
  });

  it("turns the API's error shape into an ApiError", async () => {
    fakeApi({
      "GET /api/runs/9": () =>
        reply(404, { error: { code: "not_found", message: "run 9 not found", details: null } }),
    });

    const error = (await api.get("/api/runs/9").catch((e) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect([error.status, error.code, error.message]).toEqual([
      404,
      "not_found",
      "run 9 not found",
    ]);
  });
});

describe("describeError", () => {
  it("names the bad fields of a 422", () => {
    const error = new ApiError(422, "validation_error", "the request has invalid fields", [
      { loc: ["body", "source_options", "radius_km"], msg: "must be at most 50" },
      { loc: ["body"], msg: "bad body" },
    ]);
    expect(describeError(error)).toBe(
      "the request has invalid fields: source_options.radius_km: must be at most 50; bad body",
    );
  });

  it("explains a network failure", () => {
    expect(describeError(new TypeError("Failed to fetch"))).toBe(
      "can't reach the API (is it running?)",
    );
  });
});
