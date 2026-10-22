/**
 * papers adapter utilities — date-windowed paper metadata via OpenAlex.
 *
 * 为什么需要它：arXiv 官方 API 在国内常被 429 限流；OpenAlex 无速率限制，
 * 且支持 from_publication_date 过滤 + 摘要（abstract_inverted_index 还原）。
 */
import { ArgumentError, CommandExecutionError, EmptyResultError } from '@jackwener/opencli/errors';

export const OPENALEX_BASE = 'https://api.openalex.org/works';
const MAILTO = 'rl-daily-digest@example.com';

const SELECT_FIELDS = [
    'id', 'doi', 'title', 'publication_year', 'publication_date', 'cited_by_count',
    'authorships', 'primary_location', 'open_access', 'type', 'abstract_inverted_index',
].join(',');

/** OpenAlex 摘要以 token→position 的倒排索引存储，需还原为纯文本。 */
export function reconstructAbstract(invertedIndex) {
    if (!invertedIndex || typeof invertedIndex !== 'object') return '';
    const positions = [];
    for (const [token, idxs] of Object.entries(invertedIndex)) {
        for (const i of idxs) positions.push([i, token]);
    }
    if (!positions.length) return '';
    positions.sort((a, b) => a[0] - b[0]);
    return positions.map((p) => p[1]).join(' ').replace(/\s+/g, ' ').trim();
}

export function bareId(url) {
    const m = String(url || '').match(/W\d+/i);
    return m ? m[0] : String(url || '');
}

export function bareDoi(url) {
    const m = String(url || '').match(/10\.\d{4,9}\/[^\s"']+/i);
    return m ? m[0].replace(/[.,;]$/, '') : '';
}

export async function openalexFetchWindow(query, fromDate, limit) {
    const url = `${OPENALEX_BASE}?search=${encodeURIComponent(query)}` +
        `&filter=from_publication_date:${fromDate}` +
        `&per-page=${limit}&sort=publication_date:desc&select=${SELECT_FIELDS}` +
        `&mailto=${MAILTO}`;
    let resp;
    try {
        resp = await fetch(url, { headers: { 'User-Agent': 'OpenCLI/2.0 papers-adapter' } });
    } catch (err) {
        throw new CommandExecutionError(`OpenAlex 请求失败: ${err.message}`, '检查网络连通性后重试');
    }
    if (!resp.ok) {
        throw new CommandExecutionError(`OpenAlex HTTP ${resp.status}`, '稍后重试（OpenAlex 偶发限流）');
    }
    const body = await resp.json();
    return Array.isArray(body?.results) ? body.results : [];
}

/** 把 OpenAlex work 归一化成论文元数据 */
export function normalizeWork(w) {
    const doi = bareDoi(w.doi);
    const arxivId = /arxiv/i.test(doi) ? doi.toLowerCase().split('arxiv.')[1].trim() : '';
    const authors = Array.isArray(w.authships)
        ? w.authships.map((a) => String(a?.author?.display_name || '').trim()).filter(Boolean)
        : [];
    const oaUrl = w.open_access?.oa_url || w.primary_location?.landing_page_url || '';
    return {
        id: bareId(w.id),
        title: String(w.title || '').trim(),
        abstract: reconstructAbstract(w.abstract_inverted_index),
        authors,
        year: w.publication_year != null ? Number(w.publication_year) : null,
        published: String(w.publication_date || ''),
        venue: String(w.primary_location?.source?.display_name || '').trim(),
        doi,
        arxiv_id: arxivId,
        citations: w.cited_by_count != null ? Number(w.cited_by_count) : null,
        type: String(w.type || '').trim(),
        pdf: oaUrl,
        url: w.id ? `https://openalex.org/${bareId(w.id)}` : '',
    };
}

export function requireDays(value, def = 3) {
    const n = Number(value ?? def);
    if (!Number.isInteger(n) || n <= 0 || n > 30) {
        throw new ArgumentError('days must be an integer between 1 and 30');
    }
    return n;
}

export function requireLimit(value, def = 20) {
    const n = Number(value ?? def);
    if (!Number.isInteger(n) || n <= 0 || n > 100) {
        throw new ArgumentError('limit must be an integer between 1 and 100');
    }
    return n;
}

export { EmptyResultError };
