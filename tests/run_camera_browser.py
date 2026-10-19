"""Optional Chromium fake-camera smoke check. No real camera or Jev API.

Requires Playwright and a local Chromium. Screenshots are opt-in and synthetic.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mtg_jev.camera_server import make_camera_server


async def run(executable, screenshots):
    from playwright.async_api import async_playwright
    server = make_camera_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    errors = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=executable, headless=True, args=[
                '--no-sandbox', '--disable-dev-shm-usage', '--use-fake-ui-for-media-stream',
                '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'])
            context = await browser.new_context(viewport={'width': 1440, 'height': 1140})
            await context.add_init_script('''
                window.__gumCalls=0;
                window.__nativeMedia=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                navigator.mediaDevices.getUserMedia=async constraints=>{
                    window.__gumCalls++;window.__lastConstraints=constraints;
                    const stream=await window.__nativeMedia(constraints);
                    window.__testStream=stream;return stream;
                };
            ''')
            page = await context.new_page()
            page.on('pageerror', lambda error: errors.append(error.stack))
            await page.goto(f'http://127.0.0.1:{server.server_port}')
            await page.get_by_role('button', name='カメラを開始', exact=True).wait_for()
            await page.wait_for_function('()=>!document.getElementById("startCamera").disabled')
            assert await page.evaluate('window.__gumCalls') == 0
            assert server.camera.frames == 0
            if screenshots:
                screenshots.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(screenshots/'camera-idle.png'), full_page=True)
            await page.locator('#method').select_option('ordinal4')
            await page.locator('#interval').select_option('2000')
            await page.locator('#startCamera').click()
            await page.wait_for_function('()=>document.getElementById("video").readyState>=2 && !document.getElementById("startInference").disabled')
            assert await page.evaluate('window.__gumCalls') == 1
            assert server.camera.frames == 0  # Starting the camera never starts inference.
            assert await page.locator('#cameraSelect option').count() >= 2
            device = await page.locator('#cameraSelect option').evaluate_all('(options)=>options.find(o=>o.value).value')
            await page.locator('#cameraSelect').select_option(device)
            assert await page.evaluate('window.__testStream.getTracks().every(t=>t.readyState==="ended")')
            assert await page.locator('#startInference').is_disabled()
            await page.locator('#startCamera').click()
            await page.wait_for_function('()=>document.getElementById("video").readyState>=2 && !document.getElementById("startInference").disabled')
            assert await page.evaluate('window.__lastConstraints.video.deviceId.exact') == device
            assert server.camera.frames == 0
            before = await page.locator('#video').evaluate('(v)=>v.currentTime')
            await asyncio.sleep(.25)
            after = await page.locator('#video').evaluate('(v)=>v.currentTime')
            assert after > before
            await page.locator('#startInference').click()
            await page.wait_for_function('()=>document.getElementById("snapshot").width>0')
            assert (await page.locator('#boxCount').inner_text()) == '3 / 0'
            assert server.camera.frames == 1
            assert (await page.locator('#apiCalls').inner_text()) == '0回'
            assert 'MOCK' in await page.locator('#modeBadge').inner_text()
            if screenshots:
                await page.screenshot(path=str(screenshots/'camera-mock.png'), full_page=True)
            await page.wait_for_function('()=>document.getElementById("overlayStatus").textContent.includes("期限切れ")', timeout=3000)
            for _ in range(250):
                if server.camera.frames >= 2: break
                await asyncio.sleep(.01)
            assert server.camera.frames >= 2  # Real browser timer, not just a first shot.
            await page.wait_for_function('()=>!document.getElementById("pauseInference").disabled')
            await page.locator('#pauseInference').click()
            paused_frames = server.camera.frames
            await asyncio.sleep(2.1)
            assert server.camera.frames == paused_frames
            assert await page.evaluate('window.__testStream.getTracks().every(t=>t.readyState==="live")')
            # Delay a response in the browser, deliberately ignoring abort after HTTP
            # completion, to verify a late result cannot resurrect the stopped boxes.
            await page.evaluate('''()=>{
                const fetchOriginal=window.fetch.bind(window);
                window.fetch=async(...args)=>{const r=await fetchOriginal(...args);
                    if(String(args[0]).startsWith('/api/camera/frame'))await new Promise(resolve=>setTimeout(resolve,400));
                    return r;};
            }''')
            await page.locator('#startInference').click()
            for _ in range(100):
                if server.camera.frames > paused_frames: break
                await asyncio.sleep(.01)
            assert server.camera.frames == paused_frames+1
            await page.locator('#stopAll').click()
            await asyncio.sleep(.55)
            assert await page.evaluate('window.__testStream.getTracks().every(t=>t.readyState==="ended")')
            assert await page.evaluate('document.getElementById("video").srcObject===null')
            assert await page.locator('#snapshot').evaluate('(c)=>c.width') == 0
            assert await page.locator('#startInference').is_disabled()
            assert await page.locator('#boxCount').inner_text() == '—'
            await page.evaluate('''()=>{navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException('fake denial','NotAllowedError');};}''')
            await page.locator('#startCamera').click()
            await page.wait_for_function('()=>document.getElementById("error").textContent.includes("許可されていません")')
            # Permission may settle after a stop: newly acquired tracks must be released.
            await page.evaluate('''()=>{navigator.mediaDevices.getUserMedia=async constraints=>{
                const s=await window.__nativeMedia(constraints);window.__testStream=s;
                await new Promise(resolve=>setTimeout(resolve,350));return s;};}''')
            await page.locator('#startCamera').click()
            await page.wait_for_function('()=>!document.getElementById("stopAll").disabled')
            await page.locator('#stopAll').click()
            await asyncio.sleep(.6)
            assert await page.evaluate('window.__testStream.getTracks().every(t=>t.readyState==="ended")')
            assert await page.evaluate('document.getElementById("video").srcObject===null')
            assert not errors, errors
            await browser.close()
        return {'browser': 'Chromium fake camera', 'real_camera_tested': False, 'jev_api_calls': 0,
                'mock_frames': server.camera.frames, 'checks': ['no_autostart', 'camera_selection',
                'smooth_video_clock', 'explicit_inference', 'confidence_labels', 'stale_overlay_expiry',
                'pause_timer', 'stop_tracks', 'late_response_ignored', 'permission_denied', 'late_permission_cleanup'],
                'screenshots': str(screenshots) if screenshots else None}
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.chromium, args.screenshots)), ensure_ascii=False))
