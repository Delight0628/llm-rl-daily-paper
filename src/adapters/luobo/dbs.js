import { cli, Strategy } from '@jackwener/opencli/registry';
import { luoboDbList, luoboLogin, resolveCredentials } from './utils.js';

cli({
    site: 'luobo',
    name: 'dbs',
    access: 'read',
    strategy: Strategy.COOKIE,
    browser: false,
    description: 'List luobolibrary download portals (paper / journal / search entries)',
    args: [
        { name: 'username', help: 'Site account (or set LUOBO_USERNAME)' },
        { name: 'password', help: 'Site password (or set LUOBO_PASSWORD)' },
        { name: 'type', type: 'int', default: 0, help: 'Only list this dbtype (1=paper entries, 0=all)' },
        { name: 'keyword', help: 'Filter by name/intro keyword' },
    ],
    columns: ['id', 'name', 'type', 'group', 'intro'],
    func: async (args) => {
        const credentials = resolveCredentials(args);
        const { cookie } = await luoboLogin(credentials);
        const entries = await luoboDbList(cookie);
        const type = Number(args.type || 0);
        const keyword = String(args.keyword || '').trim().toLowerCase();
        return entries.filter((e) => {
            if (type && e.type !== String(type)) return false;
            if (keyword && !(e.name.toLowerCase().includes(keyword) || e.intro.toLowerCase().includes(keyword))) return false;
            return true;
        });
    },
});
