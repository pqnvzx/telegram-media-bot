#!/bin/bash

TEMP_DIR="/root/bot/temp"
MAX_SIZE_GB=10
FILE_AGE_MINUTES=180
AGGRESSIVE_FILE_AGE_MINUTES=60

[ -d "$TEMP_DIR" ] || exit 0

find "$TEMP_DIR" -type f -mmin +$FILE_AGE_MINUTES -delete
find "$TEMP_DIR" -type d -empty -delete

CURRENT_SIZE_GB=$(du -sBG "$TEMP_DIR" 2>/dev/null | awk '{print $1}' | tr -d 'G')

[ -n "$CURRENT_SIZE_GB" ] || exit 0

if [ "$CURRENT_SIZE_GB" -ge "$MAX_SIZE_GB" ]; then
    find "$TEMP_DIR" -type f -mmin +$AGGRESSIVE_FILE_AGE_MINUTES -delete
    find "$TEMP_DIR" -type d -empty -delete
fi
