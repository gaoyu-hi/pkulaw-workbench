import type {
  LoadURLOptions,
  PostBody,
  Referrer
} from "electron";

export interface ChildNavigationRequest {
  postBody?: PostBody | null;
  referrer?: Referrer | null;
}

export function controlledChildLoadOptions(
  request: ChildNavigationRequest
): LoadURLOptions {
  const options: LoadURLOptions = {};
  const postBody = request.postBody;
  if (postBody) {
    options.postData = postBody.data;
    const boundary =
      postBody.boundary &&
      !/;\s*boundary=/i.test(postBody.contentType)
        ? `; boundary=${postBody.boundary}`
        : "";
    options.extraHeaders = `Content-Type: ${postBody.contentType}${boundary}\n`;
  }
  if (request.referrer?.url) {
    options.httpReferrer = request.referrer;
  }
  return options;
}

export function shouldCloseSettledBatchChild(
  openedUrl: string,
  currentUrl: string
): boolean {
  const opened = openedUrl.trim().toLowerCase();
  const openedAsBatch =
    opened.includes("/batchdownload.html") ||
    opened.includes("/download/downloadcatalog");
  if (!openedAsBatch) {
    return false;
  }
  const current = currentUrl.trim().toLowerCase();
  return (
    !current ||
    current === "about:blank" ||
    current.includes("/batchdownload.html") ||
    current.includes("/download/downloadcatalog")
  );
}
