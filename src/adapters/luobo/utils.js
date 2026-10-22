/**
 * luobolibrary adapter utilities.
 *
 * 萝卜图书馆（lib.luobolibrary.cn）是一个论文下载门户：登录后可以解析各出版商 /
 * 检索站（知网、维普、万方、Elsevier、IEEE、Springer、学术搜索等）的真实入口地址。
 * 站点后端是 PHP：
 *   POST /login.php      {username,password,deviceType} -> {code,msg,session,type,...}
 *   GET  /getDbList.php  -> 入口（数据库）列表
 *   GET  /Auth.php?itemid=N -> {code,data:"/rk.php?itemId=N"}
 *   GET  /rk.php?itemId=N  -> 真实入口地址（location.href / frame src / meta refresh）
 */
import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';

export const LUOBO_BASE = 'http://lib.luobolibrary.cn';
const UA = 'Mozilla/5.0 (compatible; OpenCLI/2.0; luobo-adapter)';

/** 站点账号：优先读环境变量，其次读命令行参数。 */
export function resolveCredentials(args = {}) {
    const username = String(args.username || process.env.LUOBO_USERNAME || '').trim();
    const password = String(args.password || process.env.LUOBO_PASSWORD || '').trim();
    if (!username || !password) {
        throw new ArgumentError(
            '缺少站点账号密码：请提供 --username/--password，或设置环境变量 LUOBO_USERNAME / LUOBO_PASSWORD'
        );
    }
    return { username, password };
}

/** 统一的站点请求：返回 {json|text, cookies} */
export async function luoboRequest(path, { method = 'GET', body = null, cookie = null } = {}) {
    const headers = {
        'User-Agent': UA,
        'Accept': 'application/json, text/plain, */*',
        'Referer': `${LUOBO_BASE}/`,
    };
    if (cookie) headers.Cookie = cookie;
    if (body) headers['Content-Type'] = 'application/json';

    let resp;
    try {
        resp = await fetch(`${LUOBO_BASE}${path}`, {
            method,
            headers,
            body: body ? JSON.stringify(body) : undefined,
            redirect: 'follow',
        });
    } catch (err) {
        throw new CommandExecutionError(
            `luobo 请求失败: ${path} (${err.message})`,
            '检查网络连通性，或稍后重试（站点偶发限流）'
        );
    }
    const text = await resp.text();
    const setCookies = resp.headers.getSetCookie ? resp.headers.getSetCookie() : [];
    const cookies = setCookies.map((c) => c.split(';')[0]).join('; ');
    if (!resp.ok) {
        throw new CommandExecutionError(
            `luobo HTTP ${resp.status} (${path})`,
            '站点可能临时不可用或限流，稍后重试'
        );
    }
    let json = null;
    try {
        json = JSON.parse(text);
    } catch {
        json = null;
    }
    return { json, text, cookies };
}

/** 登录并返回会员信息 + 会话 cookie。 */
export async function luoboLogin(credentials) {
    const { json, cookies } = await luoboRequest('/login.php', {
        method: 'POST',
        body: { username: credentials.username, password: credentials.password, deviceType: 'pc' },
    });
    if (!json || json.code !== 200) {
        throw new CommandExecutionError(
            `luobo 登录失败: ${(json && json.msg) || '账号或密码错误'}`,
            '核对站点账号密码；部分入口需要更高等级会员权限'
        );
    }
    if (!cookies) {
        throw new CommandExecutionError('luobo 登录未返回会话 cookie', '站点可能改版，稍后重试');
    }
    return {
        member: {
            type: json.type || '',
            member_type: MEMBER_TYPES[String(json.type)] || '未知',
            expiration: json.expiration || '',
            cardtime: json.cardtime || '',
            rename: json.rename || '',
        },
        cookie: cookies,
    };
}

export const MEMBER_TYPES = {
    1: '中文会员',
    2: '英文会员',
    3: '中英文会员',
};

/** 入口（数据库）列表 */
export async function luoboDbList(cookie) {
    const { json } = await luoboRequest('/getDbList.php', { cookie });
    if (!Array.isArray(json)) {
        throw new CommandExecutionError('luobo 入口列表返回异常', '站点接口可能已变更，稍后重试');
    }
    return json.map((d) => ({
        id: String(d.id ?? ''),
        name: d.dbname || '',
        type: String(d.dbtype ?? ''),
        group: String(d.p_id ?? ''),
        intro: d.db_intro || '',
        intro2: d.db_intro2 || '',
    }));
}

/** 从 HTML 中提取真实跳转地址 */
export function extractTargetUrl(html, fallback = '') {
    const patterns = [
        /location\.href\s*=\s*["']([^"']+)["']/i,
        /location\s*=\s*["']([^"']+)["']/i,
        /<frame[^>]+src=["']([^"']+)["']/i,
        /content=["']\d+;\s*url=([^"']+)["']/i,
        /<iframe[^>]+src=["'](https?:\/\/[^"']+)["']/i,
    ];
    for (const p of patterns) {
        const m = html.match(p);
        if (m && m[1]) return m[1].trim();
    }
    const firstHttp = html.match(/https?:\/\/[^\s"'<>\\)]+/i);
    if (firstHttp) return firstHttp[0];
    return fallback;
}

/** 解析某个入口的真实地址：Auth.php -> rk.php -> 跟随跳转 */
export async function luoboResolveEntry(itemId, cookie) {
    const { json } = await luoboRequest(`/Auth.php?itemid=${encodeURIComponent(itemId)}`, { cookie });
    if (!json || json.code !== 200) {
        throw new CommandExecutionError(
            `luobo 入口鉴权失败: ${(json && json.msg) || json?.code}`,
            '先登录；若账号权限不足请在站点升级会员'
        );
    }
    const rel = json.data;
    if (!rel) {
        throw new CommandExecutionError('luobo 入口未返回跳转地址', '站点接口可能已变更');
    }
    const { text } = await luoboRequest(rel, { cookie });
    const target = extractTargetUrl(text, rel);
    return { via: rel, url: toAbsolute(target) };
}

export function toAbsolute(url, base = LUOBO_BASE) {
    if (!url) return '';
    if (/^https?:\/\//i.test(url)) return url;
    if (url.startsWith('/')) return base + url;
    return `${base}/${url}`;
}

/** 按 id 或名称关键字查找入口 */
export function pickEntry(entries, { id, name }) {
    if (id !== undefined && id !== null && String(id).length > 0) {
        const found = entries.find((e) => e.id === String(id));
        if (!found) throw new ArgumentError(`未找到 id=${id} 的入口（用 luobo dbs 查看全部）`);
        return found;
    }
    const key = String(name || '').trim().toLowerCase();
    if (!key) throw new ArgumentError('需要 --id 或 --name 指定入口');
    const hit = entries.find((e) => e.name.toLowerCase().includes(key) || e.intro.toLowerCase().includes(key) || e.intro2.toLowerCase().includes(key));
    if (!hit) {
        throw new ArgumentError(`未找到名称包含 "${name}" 的入口（用 luobo dbs 查看全部）`);
    }
    return hit;
}
