FROM python:3.14-slim-trixie

# The libraries the browser links against, fontconfig and git for
# `apostate fonts install windows`, and Xvfb for headed launches.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
      libcairo2 libcups2t64 libdbus-1-3 libexpat1 libgbm1 libglib2.0-0t64 \
      libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
      libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
      fontconfig git xvfb \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir apostate

RUN useradd --create-home apostate
USER apostate
WORKDIR /home/apostate

# The browser goes to ~/.cache/apostate and the fonts to ~/.local/share/fonts.
RUN apostate install && apostate fonts install windows

# A volume mounted here keeps user data directories between containers.
RUN mkdir profiles

COPY check.py .
CMD ["python", "check.py"]
