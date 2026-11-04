import { describe, expect, it } from "vitest";
import type { PostBody, Referrer } from "electron";
import {
  controlledChildLoadOptions,
  shouldCloseSettledBatchChild
} from "../src/main/child-navigation";

describe("controlledChildLoadOptions", () => {
  it("preserves form POST data, content type, boundary and referrer", () => {
    const bytes = Buffer.from("typeName=catalogExcel&library=chl");
    const postBody = {
      contentType: "application/x-www-form-urlencoded",
      boundary: "fixture-boundary",
      data: [{ type: "rawData", bytes }]
    } as PostBody;
    const referrer = {
      url: "https://www.pkulaw.com/",
      policy: "strict-origin-when-cross-origin"
    } as Referrer;

    const options = controlledChildLoadOptions({ postBody, referrer });

    expect(options.postData).toBe(postBody.data);
    expect(options.extraHeaders).toBe(
      "Content-Type: application/x-www-form-urlencoded; boundary=fixture-boundary\n"
    );
    expect(options.httpReferrer).toBe(referrer);
  });

  it("does not invent headers for a normal GET child window", () => {
    expect(controlledChildLoadOptions({})).toEqual({});
  });

  it("does not duplicate an existing multipart boundary", () => {
    const postBody = {
      contentType: "multipart/form-data; boundary=already-present",
      boundary: "already-present",
      data: []
    } as PostBody;
    expect(
      controlledChildLoadOptions({ postBody }).extraHeaders
    ).toBe("Content-Type: multipart/form-data; boundary=already-present\n");
  });

  it("closes batch children even after they navigate to a blank URL", () => {
    expect(
      shouldCloseSettledBatchChild(
        "https://www.pkulaw.com/BatchDownload.html?id=fixture",
        ""
      )
    ).toBe(true);
    expect(
      shouldCloseSettledBatchChild(
        "https://www.pkulaw.com/Download/DownloadCatalog",
        "about:blank"
      )
    ).toBe(true);
    expect(
      shouldCloseSettledBatchChild(
        "https://www.pkulaw.com/login",
        "about:blank"
      )
    ).toBe(false);
  });
});
