import asyncio

from apostate import launch_async

READ = """() => {
    const gl = document.createElement("canvas").getContext("webgl");
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    return [
        `${navigator.hardwareConcurrency} cores`,
        `${screen.width}x${screen.height}`,
        gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
    ].join(", ");
}"""


async def session(seed):
    async with await launch_async(fingerprint=seed, fingerprint_platform="windows") as browser:
        page = await browser.new_page()
        await page.goto("https://example.com")
        return seed, await page.evaluate(READ)


async def main():
    results = await asyncio.gather(*(session(seed) for seed in (1, 2, 3)))
    for seed, machine in results:
        print(f"seed {seed}  {machine}")


asyncio.run(main())
