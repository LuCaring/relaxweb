const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const source = fs.readFileSync(path.join(__dirname, '../assets/js/dungeon/dgn-beta-crafting.js'), 'utf8');
  const model = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  const rand = () => 0.5;
  const normal = model.makeGear('weapon', 'starter_blade', 1, rand, 'normal');
  assert.deepEqual(normal.affixes, []);
  assert.deepEqual(new Set(model.eligibleAffixes(normal).map((row) => row.tier)), new Set([3]));
  const high = { ...normal, itemLevel: 65 };
  assert.deepEqual(new Set(model.eligibleAffixes(high).map((row) => row.tier)), new Set([1, 2, 3]));
  const odds = model.affixOdds(high);
  assert.ok(Math.abs(odds.reduce((sum, row) => sum + row.probability, 0) - 1) < 1e-12);

  const magic = model.craftGear(high, 'transmutation', rand);
  assert.equal(magic.rarity, 'magic');
  assert.equal(magic.affixes.length, 1);
  const augmented = model.craftGear(magic, 'augmentation', rand);
  assert.deepEqual(new Set(augmented.affixes.map((row) => row.kind)), new Set(['prefix', 'suffix']));
  const rare = model.craftGear(augmented, 'regal', rand);
  assert.equal(rare.affixes.length, 3);
  assert.equal(model.craftGear(rare, 'exalted', rand).affixes.length, 4);
  assert.equal(model.craftGear(rare, 'chaos', rand).affixes.length, 3);
  assert.equal(model.craftGear(rare, 'annulment', rand).affixes.length, 2);
  assert.equal(model.craftGear(rare, 'divine', rand).affixes.length, 3);
  const alchemy = model.craftGear(augmented, 'alchemy', rand);
  assert.equal(alchemy.affixes.length, 4);
  const full = model.craftGear(model.craftGear(alchemy, 'exalted', rand), 'exalted', rand);
  assert.equal(full.affixes.length, 6);
  assert.equal(model.affixOdds(full).length, 0);
  assert.throws(() => model.craftGear(full, 'exalted', rand));
  assert.throws(() => model.craftGear(high, 'exalted', rand));
  assert.equal(high.affixes.length, 0);
  console.log('Beta crafting rules OK');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
