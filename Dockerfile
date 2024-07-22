FROM python:3.9.5-slim

# Create a group and user to run our app
ARG APP_USER=appuser
RUN groupadd -r $APP_USER && useradd --no-log-init -r -g $APP_USER $APP_USER

# set environment variables
# 1st variable -> Prevents Python from writing pyc files to disc (equivalent to python -B option)
# 2nd variable -> Prevents Python from buffering stdout and stderr (equivalent to python -u option)
ENV PYTHONDONTWRITEBYTECODE 1 
ENV PYTHONUNBUFFERED=1


# set work directory
# create the appropriate directories
ENV APP_HOME=/code
RUN mkdir $APP_HOME
WORKDIR $APP_HOME

# Install build deps, then run `pip install`, then remove unneeded build deps all in a single step.
################ ADD here the dependencies install #################
COPY requirements.txt /code/
RUN set -ex \
    && BUILD_DEPS=" \
    build-essential \
    net-tools \
    libpcre3-dev \
    libpq-dev \
    " \
    && apt-get update && apt-get install -y --no-install-recommends $BUILD_DEPS \
    && pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt \
    \
    && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false $BUILD_DEPS \
    && rm -rf /var/lib/apt/lists/*

# adding the script to /code/
COPY main.py /code/


# Change to a non-root user
# RUN chown -R $APP_USER:$APP_USER $APP_HOME
# USER $APP_USER:$APP_USER

#lastly we specified the entry command this line is simply running python ./main.py in our container terminal
CMD python3 /code/main.py -f
