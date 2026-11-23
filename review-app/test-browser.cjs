const { chromium } = require('C:/Users/15809/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs');const assert=require('assert');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const context=await browser.newContext({viewport:{width:1500,height:1000}}); const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8765');await page.screenshot({path:'D:/shukong/review-app/home.png',fullPage:true});
 await page.click('#rulesButton');assert.equal(await page.locator('#rulesList input').count(),12);await page.locator('#rulesDialog .dialog-footer button').click();
 const id=fs.readFileSync('D:/shukong/review-app/test-job-id.txt','utf8').trim();await page.evaluate(id=>localStorage.setItem('drawingJob',id),id);await page.reload();
 await page.waitForSelector('.issue-card');await page.waitForFunction(()=>document.querySelector('#pageImage').complete && document.querySelector('#pageImage').naturalWidth>0);
 await page.locator('.issue-card').first().click();assert.equal(await page.locator('.annotation.active').count(),1);
 await page.screenshot({path:'D:/shukong/review-app/review.png',fullPage:true});
 await page.click('#zoomIn');assert.equal(await page.locator('#zoomLabel').innerText(),'125%');await page.click('#zoomLabel');
 await page.locator('#fileSelect').selectOption('1');await page.click('#nextPage');assert.equal(await page.locator('#pageCounter').innerText(),'2 / 2');await page.waitForFunction(()=>document.querySelector('#pageImage').complete && document.querySelector('#pageImage').naturalWidth>0);
 const download=page.waitForEvent('download');await page.click('#downloadAll');assert((await download).suggestedFilename().endsWith('.zip'));
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'D:/shukong/review-app/mobile.png',fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false,'mobile horizontal overflow');
 assert.deepEqual(errors,[]);console.log('PASS: home, 12 rules, task restore, issue-to-PDF selection, zoom, multipage navigation, ZIP download, mobile layout; zero JS errors.');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
