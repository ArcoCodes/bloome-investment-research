import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
const require = createRequire(import.meta.url);
const { auditVisuals } = require('../scripts/visual-audit.cjs');
const renderer = require('../dist/render-report.cjs');

const marker = (v) => `{{visual:${v.key}}}`;
const evidence = [{id:'test', title:'合成测试数据，非投资研究', quote:'仅用于组件回归'}];
export function baselineFixture() {
  const visuals = [];
  for (let i=0; i<18; i++) visuals.push({key:`comparison-${i}`,type:'bar',title:`回归样例 ${i+1}：比较两项指标`,unit:'测试单位',aria_label:'合成测试，不是投资数据',evidence_ids:['test'],items:[{label:'指标甲',value:10+i,display:String(10+i)},{label:'指标乙',value:20+i,display:String(20+i)}]});
  visuals.push({key:'sensitivity',type:'matrix',title:'两变量测试矩阵',aria_label:'合成测试矩阵',evidence_ids:['test'],columns:['低','高'],rows:[{label:'低',values:['10','20']},{label:'高',values:['20','30']}]});
  for(let i=0;i<5;i++) visuals.push({key:`mechanism-${i}`,type:'flow',title:`机制传导测试 ${i+1}`,aria_label:'机制组件测试',evidence_ids:['test'],nodes:[{label:`投入 ${i+1}`},{label:'利用率',detail:'连接产能与产出'},{label:'现金回报',detail:'需求不足会中断传导'}]});
  for(let i=0;i<3;i++) visuals.push({key:`lookup-${i}`,type:'table',title:`查阅表 ${i+1}`,aria_label:'表格组件测试',evidence_ids:['test'],columns:['项目','条件'],rows:[[`项目${i+1}`,'验证结果']]});
  const markdown = '# 版式与图表回归 · 非投资报告\n\n研究期限：未来12–24个月  \n估值币种：美元  \n研究截止日：2026-09-08\n\n## 增长可信，但现金回报尚待兑现\n\n这是普通正文结论，没有高亮框或自动命名。以下图表使用合成测试数据，仅验证组件数量、排布和完整性。\n\n' + visuals.map((v,i)=>marker(v)+(i%2===1?'\n\n这组图检查比较关系。下一组继续验证不同指标的布局与完整性。':'')).join('\n\n');
  return {markdown,visuals:{visuals},evidence};
}
test('NVIDIA baseline counts quantitative, causal and lookup views separately',()=>{
  const f=baselineFixture();const result=auditVisuals(f.markdown,f.visuals);
  assert.deepEqual(result.counts,{total:27,quantitative:19,causal:5,tables:3,grammars:4});
  assert.deepEqual(result.errors,[]);
  const insufficient=auditVisuals(f.markdown,{visuals:f.visuals.visuals.slice(0,8)});
  assert.deepEqual(insufficient.errors,[]);
  assert.ok(insufficient.warnings.some(e=>e.includes('coverage review')));
  const duplicate={...f.visuals.visuals[0],key:'duplicate'};
  assert.ok(auditVisuals(f.markdown+'\n'+marker(duplicate),{visuals:[...f.visuals.visuals,duplicate]}).errors.some(e=>e.includes('Duplicate')));
});
test('all 27 planned figures survive rendering once; no automatic judgment',()=>{
  const f=baselineFixture();const html=renderer.renderReport({...f,coverage:{},template:'<style></style>'});
  for(const v of f.visuals.visuals) assert.equal(html.split(`data-visual-key="${v.key}"`).length-1,1);
  assert.doesNotMatch(html,/judge-box|judge-label|核心判断/);
  assert.equal(html.split('研究截止日').length-1,1);
  assert.match(html,/<h2 class="section-label">增长可信/);
});
test('consecutive odd and even visual runs never drop trailing figures',()=>{
  for(const count of [3,4,5,8]){
    const f=baselineFixture();f.visuals.visuals=f.visuals.visuals.slice(0,count);
    f.markdown='# Test\n\n## Comparison\n\n'+f.visuals.visuals.map(marker).join('\n\n');
    const html=renderer.renderReport({...f,coverage:{},template:'<style></style>'});
    assert.equal((html.match(/<figure /g)||[]).length,count);
    assert.equal((html.match(/class="viz-pair"/g)||[]).length,Math.floor(count/2));
  }
});
test('unheaded preamble is preserved without relabeling; missing title is explicit',()=>{
  const html=renderer.renderReport({markdown:'# 明确标题\n\n只有引言，不生成判断标题。',template:'<style></style>'});
  assert.match(html,/只有引言/);assert.doesNotMatch(html,/核心判断|judge-box/);
  assert.throws(()=>renderer.inspectReport('没有标题'),/explicit H1/);
});
