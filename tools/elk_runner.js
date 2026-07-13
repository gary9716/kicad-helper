// tools/elk_runner.js
// Reads an ELK JSON graph on stdin, runs elkjs layout, writes layouted JSON to stdout.
// Usage: node tools/elk_runner.js < graph.json > layouted.json
const ELK = require('elkjs');
const elk = new ELK();

let input = '';
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', () => {
  elk.layout(JSON.parse(input))
    .then((result) => { process.stdout.write(JSON.stringify(result)); })
    .catch((err) => {
      process.stderr.write(String(err && err.stack ? err.stack : err));
      process.exit(1);
    });
});
