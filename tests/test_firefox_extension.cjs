// Real Firefox temporary add-on; a synthetic X page, no session and no backend calls.
const assert=require('node:assert/strict');
const net=require('node:net');
const path=require('node:path');
const {firefox}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const extension=process.env.FIREFOX_EXTENSION;
if(!extension)throw Error('FIREFOX_EXTENSION must name the generated Firefox extension folder.');
async function freePort(){const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const port=server.address().port;await new Promise(resolve=>server.close(resolve));return port;}
function remote(port){return new Promise((resolve,reject)=>{
 const socket=net.connect(port,'127.0.0.1');let buffer=Buffer.alloc(0);const queue=[],waiting=[];
 socket.on('error',reject);
 const next=()=>queue.length?Promise.resolve(queue.shift()):new Promise(resolve=>waiting.push(resolve));
 socket.on('data',chunk=>{buffer=Buffer.concat([buffer,chunk]);while(true){const colon=buffer.indexOf(58);if(colon<0)return;const size=Number(buffer.subarray(0,colon).toString());if(buffer.length<colon+1+size)return;const message=JSON.parse(buffer.subarray(colon+1,colon+1+size));buffer=buffer.subarray(colon+1+size);if(waiting.length)waiting.shift()(message);else queue.push(message);}});
 socket.once('connect',()=>resolve({socket,next,send:message=>{const body=JSON.stringify(message);socket.write(Buffer.byteLength(body)+':'+body);}}));
 });}
(async()=>{
 const port=await freePort();let browser,rdp;
 const timeout=setTimeout(async()=>{console.error('Firefox integration timed out');rdp?.socket.destroy();try{await browser?.close();}finally{process.exit(1);}},45000);
 try{
 browser=await firefox.launch({headless:true,executablePath:process.env.FIREFOX_PATH,args:['--start-debugger-server',String(port)],firefoxUserPrefs:{'devtools.debugger.remote-enabled':true,'devtools.debugger.prompt-connection':false,'xpinstall.signatures.required':false}});
 rdp=await remote(port);await rdp.next();rdp.send({to:'root',type:'getRoot'});let root;
 do{root=await rdp.next();}while(!root.addonsActor);
 rdp.send({to:root.addonsActor,type:'installTemporaryAddon',addonPath:path.resolve(extension)});
 let installed;do{installed=await rdp.next();}while(installed.from!==root.addonsActor);
 assert.equal(installed.error,undefined,JSON.stringify(installed));
 assert.equal(installed.addon?.id||installed.id,'archive-x@local.extension');
 const context=await browser.newContext({viewport:null});const page=await context.newPage();
 await context.route('**/*',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html><body><article data-testid="tweet"><div data-testid="User-Name"><a href="https://x.com/demo/status/123"><time>Maintenant</time></a></div><p>Post fictif</p><div role="group"><button data-testid="like">Like</button><button data-testid="retweet">Repost</button></div></article></body></html>'}));
 await page.goto('https://x.com/home');
 const button=page.locator('.zevent-archive-button');await button.waitFor({timeout:10000});
 await button.click();
 await page.waitForFunction(()=>document.querySelector('.zevent-archive-button')?.dataset.state==='retry',{},{timeout:10000});
 assert.match(await button.getAttribute('title'),/connect|connexion|pair|application|archive/i);
 console.log('PASS real Firefox: generated MV3 add-on installed; X content script, archive button and background messaging respond without a paired session. No backend or real X traffic.');
 }finally{rdp?.socket.destroy();await browser?.close();clearTimeout(timeout);}
})().catch(error=>{console.error(error);process.exitCode=1;});
