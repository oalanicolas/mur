export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!['GET', 'HEAD'].includes(request.method)) return new Response('Método não permitido', {status: 405, headers: {Allow: 'GET, HEAD'}});
    if (!url.pathname.startsWith('/downloads/')) return env.ASSETS.fetch(request);
    const name = url.pathname.slice('/downloads/'.length);
    if (!/^MUR-\d+\.\d+\.\d+(?:-beta\.\d+)?-universal\.(zip|dmg)$/.test(name)) return new Response('Arquivo não encontrado', {status: 404});
    const key = 'downloads/' + name;
    const object = request.method === 'HEAD'
      ? await env.RELEASES.head(key)
      : await env.RELEASES.get(key, {range: request.headers, onlyIf: request.headers});
    if (!object) return new Response('Arquivo não encontrado', {status: 404});
    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set('Content-Type', name.endsWith('.dmg') ? 'application/x-apple-diskimage' : 'application/zip');
    headers.set('Content-Disposition', `attachment; filename="${name}"`);
    headers.set('ETag', object.httpEtag);
    headers.set('Cache-Control', 'public, max-age=31536000, immutable, no-transform');
    headers.set('Accept-Ranges', 'bytes');
    headers.set('X-Content-Type-Options', 'nosniff');
    if (request.method === 'GET' && !('body' in object)) return new Response(null, {status: request.headers.has('If-None-Match') ? 304 : 412, headers});
    let status = 200;
    if (request.method === 'GET' && request.headers.has('Range') && object.range) {
      const offset = object.range.offset ?? Math.max(0, object.size - object.range.suffix);
      const length = object.range.length ?? object.size - offset;
      headers.set('Content-Range', `bytes ${offset}-${offset + length - 1}/${object.size}`);
      headers.set('Content-Length', String(length));
      status = 206;
    } else headers.set('Content-Length', String(object.size));
    return new Response(request.method === 'HEAD' ? null : object.body, {status, headers});
  }
};
