import { cli, Strategy } from '@jackwener/opencli/registry';
import { luoboDbList, luoboLogin, luoboResolveEntry, pickEntry, resolveCredentials } from './utils.js';

cli({
    site: 'luobo',
    name: 'entry',
    access: 'read',
    strategy: Strategy.COOKIE,
    browser: false,
    description: 'Resolve the real download/search URL of a luobolibrary portal entry',
    args: [
        { name: 'id', help: 'Entry id (see: luobo dbs)' },
        { name: 'name', help: 'Entry name keyword, e.g. 学术搜索 / 标准下载 / IEEE' },
        { name: 'username', help: 'Site account (or set LUOBO_USERNAME)' },
        { name: 'password', help: 'Site password (or set LUOBO_PASSWORD)' },
    ],
    columns: ['id', 'name', 'url', 'via'],
    func: async (args) => {
        const credentials = resolveCredentials(args);
        const { cookie } = await luoboLogin(credentials);
        const entries = await luoboDbList(cookie);
        const entry = pickEntry(entries, { id: args.id, name: args.name });
        const resolved = await luoboResolveEntry(entry.id, cookie);
        return {
            id: entry.id,
            name: entry.name,
            intro: entry.intro,
            url: resolved.url,
            via: resolved.via,
        };
    },
});
