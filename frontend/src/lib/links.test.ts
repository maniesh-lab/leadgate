/** Outside links: only http(s) is clickable, and a bare website gets https. */
import { describe, expect, it } from "vitest";
import { webLink } from "./links";

describe("webLink", () => {
  it("keeps http(s) and adds https to a bare website", () => {
    expect(webLink("https://smile.test/about")).toBe("https://smile.test/about");
    expect(webLink("http://smile.test")).toBe("http://smile.test/");
    expect(webLink(" www.smile.test ")).toBe("https://www.smile.test/");
    expect(webLink("smile.test:8080/x")).toBe("https://smile.test:8080/x");
  });

  it("gives no link for anything else", () => {
    for (const bad of ["javascript:alert(1)", "java\tscript:alert(1)", "data:text/html,x"]) {
      expect(webLink(bad)).toBeNull();
    }
    expect(webLink("mailto:hi@smile.test")).toBeNull();
    expect(webLink("")).toBeNull();
    expect(webLink(null)).toBeNull();
    expect(webLink("http://[::1")).toBeNull();
  });
});
