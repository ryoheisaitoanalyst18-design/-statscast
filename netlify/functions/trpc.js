'use strict';

const fs = require('fs');
const path = require('path');
const https = require('https');

// Data files are co-located in ./data/ next to this function file
const DATA_DIR = path.join(__dirname, 'data');
const UPSTREAM = 'rokudaistats-hwdmt5h4.manus.space';

function fetchUpstream(urlPath) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: UPSTREAM,
      path: urlPath,
      method: 'GET',
      headers: { 'Accept': 'application/json' },
    };
    const req = https.request(options, (res) => {
      let body = '';
      res.on('data', chunk => { body += chunk; });
      res.on('end', () => resolve(body));
    });
    req.on('error', reject);
    req.end();
  });
}

exports.handler = async (event) => {
  const headers = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
  };

  try {
    // Extract procedure name from path: /api/trpc/statcast.years => statcast.years
    const procedure = (event.path || '').replace(/^\/api\/trpc\//, '');
    const query = event.queryStringParameters || {};
    const isBatch = query.batch === '1';

    // Parse input
    let input = {};
    if (query.input) {
      try {
        const parsed = JSON.parse(decodeURIComponent(query.input));
        input = isBatch ? (parsed['0']?.json || {}) : (parsed?.json || parsed);
      } catch (_) {}
    }

    let result;

    if (procedure === 'statcast.years') {
      const raw = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'years.json'), 'utf8'));
      result = raw[0].result.data.json;

    } else if (procedure === 'statcast.yearData') {
      const year = input.year || 'All';
      // Sanitize year to prevent path traversal
      const safeYear = year.replace(/[^a-zA-Z0-9]/g, '');
      const file = path.join(DATA_DIR, `yearData_${safeYear}.json`);
      if (!fs.existsSync(file)) {
        const fallback = path.join(DATA_DIR, 'yearData_All.json');
        const raw = JSON.parse(fs.readFileSync(fallback, 'utf8'));
        result = raw[0].result.data.json;
      } else {
        const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
        result = raw[0].result.data.json;
      }

    } else {
      // Proxy to upstream for player-specific data (batterDetail, batterZones, pitcherDetail)
      const inputParam = query.input
        ? encodeURIComponent(decodeURIComponent(query.input))
        : encodeURIComponent('{"0":{"json":{}}}');
      const upstreamPath = `/api/trpc/${procedure}?batch=1&input=${inputParam}`;
      const body = await fetchUpstream(upstreamPath);
      return { statusCode: 200, headers, body };
    }

    const response = isBatch
      ? [{ result: { data: { json: result } } }]
      : { result: { data: { json: result } } };

    return { statusCode: 200, headers, body: JSON.stringify(response) };

  } catch (err) {
    console.error('trpc function error:', err);
    return {
      statusCode: 500,
      headers,
      body: JSON.stringify([{ error: { message: err.message } }]),
    };
  }
};
