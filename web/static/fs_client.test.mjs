import test from 'node:test';
import assert from 'node:assert/strict';
import { fs } from './fs_client.mjs';

function successMock(last) {
  return async (url, options) => {
    last.url = url;
    last.options = options;
    return {
      ok: true,
      status: 200,
      async text() { return 'body'; },
      async json() { return { ok: true }; },
    };
  };
}

test('read builds GET /fs/read?path=<encoded> and returns text', async () => {
  const last = {};
  const saved = globalThis.fetch;
  globalThis.fetch = successMock(last);
  try {
    const result = await fs.read('a/b c.txt');
    assert.equal(last.url, '/fs/read?path=' + encodeURIComponent('a/b c.txt'));
    assert.equal(result, 'body');
  } finally {
    globalThis.fetch = saved;
  }
});

test('write posts to /fs/write with JSON body and headers', async () => {
  const last = {};
  const saved = globalThis.fetch;
  globalThis.fetch = successMock(last);
  try {
    await fs.write('p.txt', 'data', { noOverwrite: true });
    assert.equal(last.url, '/fs/write');
    assert.equal(last.options.method, 'POST');
    assert.equal(last.options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(last.options.body), { path: 'p.txt', contents: 'data', noOverwrite: true });
  } finally {
    globalThis.fetch = saved;
  }
});

test('list builds GET /fs/list?path=<encoded> and returns parsed JSON', async () => {
  const last = {};
  const saved = globalThis.fetch;
  globalThis.fetch = successMock(last);
  try {
    const result = await fs.list('my/dir');
    assert.equal(last.url, '/fs/list?path=' + encodeURIComponent('my/dir'));
    assert.deepEqual(result, { ok: true });
  } finally {
    globalThis.fetch = saved;
  }
});

test('mkdir posts to /fs/mkdir with JSON body {path}', async () => {
  const last = {};
  const saved = globalThis.fetch;
  globalThis.fetch = successMock(last);
  try {
    await fs.mkdir('d');
    assert.equal(last.url, '/fs/mkdir');
    assert.equal(last.options.method, 'POST');
    assert.equal(last.options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(last.options.body), { path: 'd' });
  } finally {
    globalThis.fetch = saved;
  }
});

test('error path: !res.ok causes read to throw', async () => {
  const saved = globalThis.fetch;
  globalThis.fetch = async (url, options) => ({
    ok: false,
    status: 400,
    async json() { return { error: 'bad' }; },
    async text() { return 'bad'; },
  });
  try {
    await assert.rejects(() => fs.read('x'));
  } finally {
    globalThis.fetch = saved;
  }
});
