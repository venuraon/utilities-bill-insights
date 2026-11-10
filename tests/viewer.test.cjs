// Dependency-free interaction checks. This DOM harness is not a visual browser test.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'output/demo.html'), 'utf8');
class Element {
  constructor(id = '') { this.id=id; this.dataset={}; this.attributes={}; this.children=[]; this.handlers={}; this.hidden=false; this.classList={toggle(){}}; }
  setAttribute(k,v){this.attributes[k]=v;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(k,v){this.handlers[k]=v;}
  focus(){this.focused=true;}
  click(){this.handlers.click?.();}
}
const elements = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element(m[1])]));
const data = html.match(/<script id="demo-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
elements.get('demo-data').textContent=data;
const tabs=['usage','invoice','email'].map(tab=>{const e=elements.get(`tab-${tab}`);e.dataset.tab=tab;return e;});
const context={
  document:{
    getElementById(id){assert.ok(elements.has(id),`Missing real HTML element: ${id}`);return elements.get(id);},
    querySelectorAll(selector){return selector==='[data-tab]'?tabs:elements.get('home-buttons').children;},
    createElement(){return new Element();}, title:''
  },
  location:{hash:'#home=C&view=usage'}, history:{replaceState(){}},
  URLSearchParams, Intl
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root,'web/viewer.js'),'utf8'),context);
assert.equal(elements.get('home-buttons').children.length,5);
const reports=JSON.parse(data).homes;
assert.ok(reports[0].weather_chart_html.includes('degrees Fahrenheit for all five homes'));
for(const report of reports){
  const id=report.home.id;
  elements.get('home-buttons').children.find(e=>e.dataset.home===id).click();
  assert.ok(elements.get('home-size').textContent.includes(`Home ${id}`));
  assert.equal(report.weather_chart_html,reports[0].weather_chart_html);
  assert.equal(elements.get('weather-chart').innerHTML,report.weather_chart_html);
  assert.ok(elements.get('weather-response').textContent.includes('Same Coruscant weather'));
  assert.equal(elements.get('bill-total').textContent,new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(report.invoice.total));
  assert.ok(elements.get('invoice-content').innerHTML.includes(`Home ${id}`));
  if(report.analysis.needs_review){
    assert.ok(elements.get('email-content').innerHTML.includes('Customer email withheld'));
    assert.ok(elements.get('mode-notice').textContent.includes('STAFF REVIEW REQUIRED'));
  }else{
    assert.ok(elements.get('email-content').innerHTML.includes(`home-${id.toLowerCase()}@example.com`));
    assert.equal(elements.get('finding-body').textContent,report.customer_insight.sentence);
    assert.equal(elements.get('finding-headline').textContent,report.customer_insight.title);
    assert.ok(elements.get('email-content').innerHTML.includes('POSSIBLE REASON &amp; NEXT STEP'));
  }
  assert.equal(elements.get('plan-message').innerHTML,report.plan_insight_html);
  assert.ok(elements.get('neighborhood-content').innerHTML.includes(report.neighborhood.insight));
  assert.equal(elements.get('email-link').href,`emails/${id}-2026-03.html`);
  assert.equal(elements.get('analysis-link').href,`analyses/${id}.json`);
  for(const tab of tabs){
    tab.click();
    assert.equal(tab.attributes['aria-selected'],'true');
    for(const other of tabs)assert.equal(elements.get(`panel-${other.dataset.tab}`).hidden,other!==tab);
  }
  for(const url of [elements.get('email-link').href,elements.get('jan-link').href,elements.get('feb-link').href]){
    assert.ok(fs.existsSync(path.join(root,'output',url)),`Missing artifact link ${url}`);
  }
}
tabs[2].handlers.keydown({key:'ArrowRight',preventDefault(){}});
assert.equal(tabs[0].attributes['aria-selected'],'true');
assert.ok(tabs[0].focused);
const lastReport=reports[reports.length-1];
const notice=lastReport.analysis.needs_review?'STAFF REVIEW REQUIRED':lastReport.provenance.mode==='sample'?'LAYOUT PREVIEW':'GENERATED RESULTS';
assert.ok(elements.get('mode-notice').textContent.includes(notice));
vm.runInContext("demo.homes.find(h=>h.home.id==='C').analysis.needs_review=true; demo.homes.find(h=>h.home.id==='C').email_html='Customer email withheld'; renderHome('C');",context);
assert.ok(elements.get('mode-notice').textContent.includes('STAFF REVIEW REQUIRED'));
assert.equal(elements.get('email-link').textContent,'View review status ↗');
assert.ok(elements.get('pipeline-note').textContent.includes('communications agent was skipped'));
assert.equal(elements.get('email-content').innerHTML,'Customer email withheld');
elements.get('home-buttons').children.find(e=>e.dataset.home==='A').click();
assert.equal(elements.get('email-link').textContent,'Open customer email ↗');
assert.ok(!elements.get('mode-notice').textContent.includes('STAFF REVIEW REQUIRED'));
console.log('Viewer checks passed: all five homes, three tabs, keyboard navigation, matching invoices/emails, and local links.');
