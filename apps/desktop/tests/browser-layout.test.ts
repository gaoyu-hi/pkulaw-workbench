import { describe, expect, it } from "vitest";
import { browserViewBoundsFromRect } from "../src/shared/browser-layout";

describe("browser view layout", () => {
  it("does not publish an unmeasured 3 by 3 view", () => {
    expect(
      browserViewBoundsFromRect({
        left: 0,
        top: 0,
        width: 3,
        height: 3
      })
    ).toBeNull();
  });

  it("publishes the settled host rectangle", () => {
    expect(
      browserViewBoundsFromRect({
        left: 272.8,
        top: 273.8,
        width: 905.2,
        height: 639.6
      })
    ).toEqual({
      x: 273,
      y: 274,
      width: 905,
      height: 640,
      visible: true
    });
  });
});
