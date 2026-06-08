import { describe, expect, it } from "vitest";
import { formatAmerican, formatPercent } from "../src/lib/api";
import {
  computeParlayOutcome,
  potentialPayout,
} from "../src/lib/tracker";

describe("formatAmerican", () => {
  it("formats plus odds", () => {
    expect(formatAmerican(150)).toBe("+150");
  });

  it("formats minus odds", () => {
    expect(formatAmerican(-110)).toBe("-110");
  });
});

describe("formatPercent", () => {
  it("formats probability", () => {
    expect(formatPercent(0.523)).toBe("52.3%");
  });
});

describe("potentialPayout", () => {
  it("calculates plus money payout", () => {
    expect(potentialPayout(100, 350)).toBe(350);
  });

  it("calculates minus money payout", () => {
    expect(potentialPayout(100, -200)).toBe(50);
  });
});

describe("computeParlayOutcome", () => {
  it("returns pending when legs unsettled", () => {
    expect(computeParlayOutcome(["win", "pending"])).toBe("pending");
  });

  it("returns loss when any leg loses", () => {
    expect(computeParlayOutcome(["win", "loss"])).toBe("loss");
  });

  it("returns win when all legs win", () => {
    expect(computeParlayOutcome(["win", "win"])).toBe("win");
  });
});
