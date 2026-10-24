// wrangler.jsonc bundles .png files as "Data" modules, which import as ArrayBuffer.
declare module "*.png" {
  const data: ArrayBuffer;
  export default data;
}
