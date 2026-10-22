import { cli, Strategy } from '@jackwener/opencli/registry';
import { luoboDbList, luoboLogin, luoboResolveEntry, resolveCredentials } from './utils.js';

const SEARCH_ENTRY_ID = '231'; // 站点「综合搜索」入口
const RESULT_BLOCK = /<h3[^>]*class="[^"]*gs_rt[^"]*"[^>]*>([\s\S]*?)<\/h3>/gi;
const STRIP_TAGS = /<[^>]+>/g;

/** 从搜索页 HTML 中尽力解析结果（部分站点由前端渲染，可能解析不到） */
function parseResults(html) {
    const out = [];
    let m;
    RESULT_BLOCK.lastIndex = 0;
    while ((m = RESULT_BLOCK.exec(html)) !== null && out.length < 20) {
        const title = m[1].replace(STRIP_TAGS, ' ').replace(/\s+/g, ' ').trim();
        const after = html.slice(m.index, m.index + 1200);
        const link = after.match(/<a[^>]+href="(https?:\/\/[^"]+)"[^>]*>/i);
        const meta = after.match(/class="[^"]*gs_a[^"]*"[^>]*>([\s\S]*?)<\/div>/i);
        out.push({
            title,
            url: link ? link[1] : '',
            meta: meta ? meta[1].replace(STRIP_TAGS, ' ').replace(/\s+/g, ' ').trim() : '',
        });
    }
    return out;
}

cli({
    site: 'luobo',
    name: 'search',
    access: 'read',
    strategy: Strategy.COOKIE,
    browser: false,
    description: 'Search papers through the luobolibrary academic-search portal',
    args: [
        { name: 'query', positional: true, required: true, help: 'Search keyword (title / DOI / arXiv id)' },
        { name: 'limit', type: 'int', default: 10, help: 'Max results (max 20)' },
        { name: 'username', help: 'Site account (or set LUOBO_USERNAME)' },
        { name: 'password', help: 'Site password (or set LUOBO_PASSWORD)' },
    ],
    columns: ['title', 'url', 'meta'],
    func: async (args) => {
        const query = String(args.query || '').trim();
        if (!query) throw new Error('luobo search query cannot be empty');
        const limit = Math.min(Math.max(Number(args.limit || 10), 1), 20);

        const credentials = resolveCredentials(args);
        const { cookie } = await luoboLogin(credentials);
        const entries = await luoboDbList(cookie);
        const portal = entries.find((e) => e.id === SEARCH_ENTRY_ID) || entries.find((e) => e.name.includes('综合搜索'));
        if (!portal) {
            throw new Error('站点未提供综合搜索入口（id=231）');
        }
        const resolved = await luoboResolveEntry(portal.id, cookie);
        let base;
        try {
            base = new URL(resolved.url).origin;
        } catch {
            base = resolved.url.replace(/\/+$/, '');
        }
        const searchUrl = `${base}/scholar?q=${encodeURIComponent(query)}&hl=zh-cn`;

        let results = [];
        let note = '';
        try {
            const resp = await fetch(searchUrl, {
                headers: { 'User-Agent': 'Mozilla/5.0 (compatible; OpenCLI/2.0; luobo-adapter)', 'Accept-Language': 'zh-CN,zh;q=0.9' },
            });
            const html = await resp.text();
            results = parseResults(html).slice(0, limit);
            if (!results.length) note = '该检索站由前端渲染，未能直接解析出结果；请使用 search_url 在浏览器打开';
        } catch (err) {
            note = `检索页抓取失败: ${err.message}`;
        }

        return {
            query,
            portal: portal.name,
            search_url: searchUrl,
            note,
            results,
        };
    },
});
