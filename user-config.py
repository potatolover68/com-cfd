# pylint: skip-file

import os

family = "commons"
mylang = "commons"
usernames["commons"]["commons"] = "MSKbot"

authenticate["commons.wikimedia.org"] = (
    os.environ["CONSUMER_TOKEN"],
    os.environ["CONSUMER_SECRET"],
    os.environ["ACCESS_TOKEN"],
    os.environ["ACCESS_SECRET"],
)
put_throttle = 1
maxlag = 5
ignore_bot_templates = True  # I am too lazy to code for this edge case, and there should be no reason to use {{nobots}} templates on the CfD pages.
