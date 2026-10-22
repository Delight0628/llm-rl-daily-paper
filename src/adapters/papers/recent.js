import { cli, Strategy } from '@jackwener/opencli/registry';
import { EmptyResultError } from '@jackwener/opencli/errors';
import {
    normalizeWork,
    openalexFetchWindow,
    requireDays,
    requireLimit,
} from './utils.js';

function isoDaysAgo(days) {
    const d = new Date(Date.now() - days * 86400000);
    return d.toISOString().slice(0, 10);
}

cli({
    site: 'papers',
    name: 'recent',
    access: 'read',
    strategy: Strategy.PUBLIC,
    browser: false,
    description: 'Fetch papers published within the last N days (OpenAlex, date-filtered, with abstract)',
    args: [
        { name: 'query', positional: true, required: true, help: 'Search keyword, e.g. "multi-agent reinforcement learning LLM"' },
        { name: 'days', type: 'int', default: 3, help: 'Only papers published within the last N days (1-30)' },
        { name: 'limit', type: 'int', default: 20, help: 'Max results (1-100)' },
    ],
    columns: ['arxiv_id', 'title', 'published', 'venue', 'citations', 'pdf'],
    func: async (args) => {
        const query = String(args.query || '').trim();
        if (!query) throw new Error('papers recent query cannot be empty');
        const days = requireDays(args.days, 3);
        const limit = requireLimit(args.limit, 20);
        const rows = await openalexFetchWindow(query, isoDaysAgo(days), limit);
        const out = rows.map(normalizeWork);
        if (!out.length) {
            throw new EmptyResultError('papers recent', `No papers in the last ${days} day(s) matching "${query}".`);
        }
        return out;
    },
});
