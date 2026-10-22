import { cli, Strategy } from '@jackwener/opencli/registry';
import { luoboLogin, resolveCredentials } from './utils.js';

cli({
    site: 'luobo',
    name: 'account',
    access: 'read',
    strategy: Strategy.COOKIE,
    browser: false,
    description: 'Check luobolibrary account / membership status',
    args: [
        { name: 'username', help: 'Site account (or set LUOBO_USERNAME)' },
        { name: 'password', help: 'Site password (or set LUOBO_PASSWORD)' },
    ],
    columns: ['member_type', 'expiration', 'cardtime', 'rename'],
    func: async (args) => {
        const credentials = resolveCredentials(args);
        const { member } = await luoboLogin(credentials);
        return member;
    },
});
