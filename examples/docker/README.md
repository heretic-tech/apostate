# Apostate in Docker

An image with the Python package, the browser, the Windows fonts and Xvfb.
`check.py` launches a Windows persona and prints what a page reads.

Tested with Docker 29.4 (OrbStack) on an Apple M4 Max, for `linux/arm64` and
for `linux/amd64` under emulation, with apostate 0.4.3.

## Build

```sh
docker build -t apostate-example .
```

The build downloads the browser into `/home/apostate/.cache/apostate` and the
Windows fonts into `/home/apostate/.local/share/fonts`, so containers start
without downloading anything. The image is about 660 MB compressed. For an x86
server, build with `--platform linux/amd64`.

## Run

```sh
docker run --rm apostate-example
```

```text
userAgent  Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36
platform   Win32
cores      12
memory     8
languages  ['en-US', 'en']
timeZone   America/New_York
screen     [1920, 1080, 1920, 1032]
gpu        ANGLE (Intel, Intel(R) UHD Graphics 770 (0x00004680) Direct3D11 vs_5_0 ps_5_0, D3D11)
segoeUI    True
```

Headed, on the Xvfb display the package starts inside the container:

```sh
docker run --rm apostate-example python check.py --headed
```

Keep a machine, its cookies and its storage in a named volume:

```sh
docker run --rm -v apostate-profiles:/home/apostate/profiles apostate-example \
  python check.py --profile profiles/shop
```

The first run draws a machine and stores its seed in
`profiles/shop/apostate/identity`. Later runs with the same volume present the
same machine.

## Notes

- The driver the package uses passes `--disable-dev-shm-usage`, so the default
  `/dev/shm` size is enough. To run the browser binary directly, start the
  container with `--shm-size=1g` or `--ipc=host`, and pass `--no-sandbox`.
- The persona's core count is capped at the CPUs the container may run on.
  `--cpuset-cpus=0,1` gives 2 cores; `--cpus=2` changes nothing.
- The image runs as the user `apostate`. The driver also passes
  `--no-sandbox`, so the container needs no extra capability.

The guide at https://docs.apostate.dev/guides/docker explains each step.
