const $ = id => document.getElementById(id);
const states = {idle:'準備できました',starting:'APIへ接続中',resuming:'再開処理中',running:'集中中',paused:'一時停止中',ended:'終了',completed:'おつかれさまでした'};
const labels = {dance_test:'ダンスの音をテスト',idle_reaction:'気まぐれな仕草',focus_motion:'集中中のダンス',dance_interrupted:'スマホ保持でダンスを中断',phone_put_down:'スマホを置いた反応',api_failed:'OpenAIへの接続失敗',remote_end_failed:'サーバー終了要求に失敗（本体は停止済み）',session_started:'集中を開始',session_paused:'一時停止',session_resumed:'集中を再開',session_ended:'終了',session_completed:'時間になりました',condition_detected:'条件を検知',condition_cleared:'条件が解消した反応',phone_detected:'スマホを検知',action_executed:'通知アクション',notification_test:'通知テスト',settings_changed:'設定を更新',observation_failed:'判定をスキップ',action_failed:'通知に失敗',action_cancel_failed:'通知停止の確認が必要'};
const reasons = {rate_limited:'API制限のため待機（30秒または設定間隔の長い方）',quota_exceeded:'OpenAIの利用残高を確認してください',model_unavailable:'Decisions API・モデルの利用権限を確認してください',insufficient_visibility:'手元が十分に見えません',decision_timeout:'画像判定がタイムアウトしました',provider_error:'OpenAIの判定処理でエラー',invalid_decision:'判定結果を確認できません',decision_refused:'画像判定が保留されました',stale_result:'古い判定結果を破棄しました',unauthorized:'OpenAIAPIキーを確認してください',service_not_configured:'OpenAIの設定待ち',session_not_found:'セッションが失効しました。終了して再度開始してください',frame_limit:'画像数上限です。終了して再度開始してください',unobservable:'手元が見えないため判定を保留',refusal:'判定を保留',authentication:'APIキーを確認してください',permission:'Decisions APIの利用権限を確認してください',timeout:'通信タイムアウト',connection:'接続待ち',invalid_response:'判定応答を確認できません',api_error:'APIエラー',capture_or_action_failed:'カメラまたは通知を確認してください'};
let initialized=false, pending=false, latest=null, imageUrl=null, connectionFailed=false, lastEventsSignature=null;
async function api(path, body) {
  const response = await fetch('/api/'+path, {cache:'no-store', ...(body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Focus-Control':'1'},body:JSON.stringify(body)})});
  if (!response.ok) { const error=await response.json().catch(()=>({})); throw Error(typeof error.detail === 'string' ? error.detail : '処理に失敗しました。'); }
  return response;
}
function showError(error){$('error').hidden=false;$('error').textContent=error.message;}
function clearPreview(){if(imageUrl){URL.revokeObjectURL(imageUrl);imageUrl=null;}$('frame').removeAttribute('src');$('frame').hidden=true;$('preview-placeholder').hidden=false;}
const conditionHints={phone:'スマホを手に持っていたら反応。机に置いたスマホは対象外です。観測時に当てはまれば反応します。',slouch:'背中が丸まり、肩や頭が前へ出た姿勢なら反応。上半身が見える位置で使ってください。観測時に当てはまれば反応します。',custom:'カメラで確認できる条件を文章で入力してください。観測時に当てはまれば反応します。'};
const conditionInputs=()=>[...document.querySelectorAll('input[name="condition"]')];
const selectedConditions=()=>conditionInputs().filter(input=>input.checked).map(input=>input.value);
function updateConditionFields(){const presets=selectedConditions(),custom=presets.includes('custom');$('custom-condition-label').hidden=!custom;$('custom-condition').disabled=!custom;$('custom-condition').required=custom;$('condition-hint').textContent=presets.map(p=>conditionHints[p]).join(' ');}

function render(s){
  latest=s;$('state').textContent=states[s.state]||s.state;
  const seconds=Math.ceil(s.remaining_seconds || s.settings.duration_minutes*60);
  $('timer').textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');
  if(['ended','completed'].includes(s.state)) $('timer').textContent='00:00';
  const server=s.server;
  $('detail').textContent=s.error ? (reasons[s.error]||s.error) : s.state==='paused' ? '観測と通知を一時停止しています。' : ['ended','completed'].includes(s.state) ? '観測と通知を終了しました。' : server ? `判定: ${server.provider==='mock'?'モック（画像解析なし）':'OpenAI'} · ${server.state==='cooldown'?'通知後の待機':server.state==='unknown'?'判定不能':server.state==='suspected'?'条件を確認中':'見守り中'}${server.state==='unknown'?' · '+(reasons[server.reason]||server.reason):''}${server.phone_probability===null?'':' · 条件の推定 '+Math.round((server.condition_probability??server.phone_probability)*100)+'%'}` : '手元が見える位置で、無理のないペースで。';
  const chosen=s.settings.conditions||[s.settings.condition]; if(chosen[0])$('detail').textContent='条件: '+chosen.map(c=>c.label).join('・')+' · '+$('detail').textContent;
  $('condition-status').textContent=(server?.conditions||[]).map(c=>`${c.condition_label}: ${c.state==='unknown'?'見える位置を確認してください':c.condition_probability>=.8?'条件を検知':c.condition_probability<=.2?'当てはまらない':'判定を保留'}`).join(' ／ ');
  const usage=s.usage;if(usage){$('usage-summary').textContent=`完了リクエスト ${usage.requests.toLocaleString()}回 · 成功 ${usage.successful_requests.toLocaleString()}回 / 失敗 ${usage.failed_requests.toLocaleString()}回`;$('usage-tokens').textContent=`入力 ${usage.input_tokens.toLocaleString()} · 出力 ${usage.output_tokens.toLocaleString()} · 合計 ${usage.total_tokens.toLocaleString()} tokens`;$('usage-note').textContent=`${new Date(usage.since).toLocaleString()} からの本体での累計。トークン数はAPI報告値で、請求金額ではありません。${usage.unreported_requests?' トークン数が返らなかった応答: '+usage.unreported_requests+'回。':''}${usage.storage_error?' 累計の保存に失敗しています。':''}`;}
  if(usage?.estimated_cost_usd!==undefined){
    const usd=usage.estimated_cost_usd;
    const amount=usd>0&&usd<0.000001?'US$0.000001 未満':'US$'+usd.toLocaleString('en-US',{minimumFractionDigits:6,maximumFractionDigits:6});
    $('usage-cost').textContent=`推定累計 ${amount}${usage.unreported_requests?'（集計できた分）':''}`;
    $('usage-cost-note').textContent=`このアプリのAPI報告使用量から計算した目安です。請求額・税・他アプリの利用分は含みません。${usage.estimated_without_cache_tokens?' 内訳不明の入力 '+usage.estimated_without_cache_tokens.toLocaleString()+' tokens は通常入力単価で概算。':''}${usage.unreported_requests?' 使用量不明の '+usage.unreported_requests+' 件は料金に未算入。':''}`;
  }
  $('count').textContent=`通知 ${s.notification_count}回`;
  const active=['running','paused','starting','resuming'].includes(s.state);
  $('start').disabled=pending||active||!s.settings.api_key_configured||!s.capabilities.camera||!$('consent').checked;
  $('pause').disabled=pending||s.state!=='running';$('resume').disabled=pending||s.state!=='paused';$('end').disabled=pending||!active;
  $('preview').disabled=pending||s.state==='running'||s.busy||!s.capabilities.camera;
  $('test').disabled=pending||s.state==='running';
  $('audio').disabled=!s.capabilities.actions.includes('reminder');$('voice').disabled=!s.capabilities.actions.includes('reminder');
  $('key-status').textContent=s.settings.api_key_configured?'（保存済み）':'（未設定）';
  $('capabilities').textContent=`ダンス: ${s.capabilities.actions.includes('dance1')?'利用可能':'未ロード'} · 😨 Fear: ${s.capabilities.actions.includes('fear1')?'利用可能':'未ロード'} · カメラ: ${s.capabilities.camera?'利用可能':'未接続'} · 声かけ: ${s.capabilities.actions.includes('reminder')?'利用可能':'未接続'}`;
  if(!initialized){$('minutes').value=s.settings.duration_minutes;$('capture-interval').value=s.settings.capture_interval_seconds??60;$('audio').checked=s.settings.audio_enabled;$('voice').checked=s.settings.voice_enabled??true;$('idle-behavior').checked=s.settings.idle_behavior_enabled??true;const presets=s.settings.condition_presets||[s.settings.condition_preset||'phone'];conditionInputs().forEach(input=>input.checked=presets.includes(input.value));$('custom-condition').value=s.settings.custom_condition||'';updateConditionFields();initialized=true;}
  if(s.events.length===0){$('event-photo-dialog').close();$('event-photo-full').removeAttribute('src');}
  const eventSignature=s.events.map(e=>e.id+(e.image_url||'')).join('|');
  if(eventSignature!==lastEventsSignature){
    lastEventsSignature=eventSignature;
  $('events').replaceChildren(...s.events.slice(-30).reverse().map(e=>{const li=document.createElement('li');li.textContent=new Date(e.time*1000).toLocaleTimeString()+' '+(labels[e.event]||e.event)+(e.condition_label?' · '+e.condition_label:'')+(e.reason?' · '+(reasons[e.reason]||e.reason):'')+(e.actions?' · '+e.actions.map(a=>a.action+': '+a.status).join(', '):'');if(e.image_url){const button=document.createElement('button');button.type='button';button.className='event-photo';button.setAttribute('aria-label','この判定に使った写真を拡大');const img=document.createElement('img');img.src=e.image_url;img.alt='判定に使った写真';img.loading='lazy';img.width=96;img.height=72;button.append(img);button.addEventListener('click',()=>{$('event-photo-full').src=e.image_url;$('event-photo-caption').textContent=new Date(e.time*1000).toLocaleTimeString()+' '+(labels[e.event]||e.event);$('event-photo-dialog').showModal();});li.append(button);}return li;}));
  if(s.state==='running')clearPreview();
  }
}
async function action(fn){pending=true;if(latest)render(latest);$('error').hidden=true;try{await fn();}catch(e){showError(e);}finally{pending=false;try{render(await(await api('status')).json());}catch(e){showError(e);}}}
$('close-event-photo').addEventListener('click',()=>{$('event-photo-dialog').close();$('event-photo-full').removeAttribute('src');});
conditionInputs().forEach(input=>input.addEventListener('change',updateConditionFields));
$('consent').addEventListener('change',()=>{if(latest)render(latest);});
document.querySelectorAll('[data-action]').forEach(b=>b.addEventListener('click',()=>action(async()=>{clearPreview();render(await(await api('session/'+b.dataset.action,{consent:$('consent').checked})).json());})));
$('settings').addEventListener('submit',e=>{e.preventDefault();action(async()=>{const presets=selectedConditions();if(!presets.length)throw Error('反応する条件を1つ以上選んでください。');const body={condition_presets:presets,custom_condition:$('custom-condition').value.trim(),duration_minutes:Number($('minutes').value),capture_interval_seconds:Number($('capture-interval').value),audio_enabled:$('audio').checked,voice_enabled:$('voice').checked,idle_behavior_enabled:$('idle-behavior').checked,motion_enabled:true};if($('api-key').value)body.api_key=$('api-key').value;initialized=false;render(await(await api('settings',body)).json());$('api-key').value='';});});
$('delete-key').addEventListener('click',()=>action(async()=>{await api('session/end',{});render(await(await api('settings',{api_key:''})).json());$('api-key').value='';}));
$('preview').addEventListener('click',()=>action(async()=>{const blob=await(await api('preview',{})).blob();clearPreview();imageUrl=URL.createObjectURL(blob);$('frame').src=imageUrl;$('frame').hidden=false;$('preview-placeholder').hidden=true;}));
$('test').addEventListener('click',()=>action(async()=>render(await(await api('test-notification',{})).json())));
async function poll(){try{if(!pending){render(await(await api('status')).json());if(connectionFailed){$('error').hidden=true;connectionFailed=false;}}}catch(e){connectionFailed=true;showError(e);$('state').textContent='本体への接続を確認中';document.querySelectorAll('[data-action]').forEach(b=>b.disabled=true);}finally{setTimeout(poll,1000);}}
poll();
