import {it,expect} from 'vitest'
import {readFileSync} from 'node:fs'
import {canonicalJson} from './canonicalJson'
import {sha256Text} from './hashCalculator'
const vectors=JSON.parse(readFileSync('tests/golden/canonical_vectors.json','utf8')) as {raw:string;canonical:string;sha256:string}[]
for(const [i,v] of vectors.entries())it('Python serialization vector '+i,()=>{expect(canonicalJson(v.raw)).toBe(v.canonical);expect(sha256Text(canonicalJson(v.raw))).toBe(v.sha256)})
it('ambiguous noncanonical float spelling fails closed',()=>expect(()=>canonicalJson('{"n":1.00}')).toThrow('Unsupported noncanonical'))
