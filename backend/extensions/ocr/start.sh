#! /bin/bash
echo $DEPLOY_ENV $APP_LOG_PATH

nohup uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1 >> $APP_LOG_PATH/main.log 2>&1 &

tail -f $APP_LOG_PATH/main.log
