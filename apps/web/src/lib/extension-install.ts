export const extensionStores = {
  chrome:
    "https://chromewebstore.google.com/detail/iihaipjedchahiehignbngclgpklbddo?utm_source=item-share-cb",
  edge: "https://microsoftedge.microsoft.com/addons/detail/devfeed/fdfidbpljbdoibphcohojmlpibaepija",
};

export function extensionBrowser(userAgent: string, protocol: string) {
  if (!/^https?:$/.test(protocol) || /Android|Mobile|iPhone|iPad/i.test(userAgent)) return null;
  if (/Edg\//.test(userAgent)) return "edge";
  if (/Chrome\//.test(userAgent) && !/OPR\/|Opera|SamsungBrowser\//.test(userAgent))
    return "chrome";
  return null;
}
