"""This file handles all interactions with Facebook on the server."""

from json import loads
from os import environ
from re import compile, sub
from time import time
import facebook
from models import get_settings

if "HEROKU" in environ:
  fb_keys = environ["fb_keys"]
else:
  from secrets import fb_keys

__author__ = "Jeffrey Chan"

def get_long_term_token(short_token, compID, instance):
    """This function gets takes in a short term access token and trades it to
    Facebook for a long term access token (expires in about 2 months).
    
    Before it does so however, for security reasons, it verifies with Facebook
    that the short term access token was actually generated by the Wix Calendar
    app. 

    Once that is done and the long term token is received, it saves this
    new token into the database along with the user's ID. 

    If a previously generated long term token is already in the database, it
    verifies that this new token belongs to the same user as the old token
    before updating the database entry.  
    """
    try:
        graph = facebook.GraphAPI(fb_keys["app_access_token"])
        verify = graph.get_object("/debug_token", input_token = short_token, \
                                  access_token = fb_keys["app_access_token"])
        verify_data = verify['data']
        if (verify_data["is_valid"] and (verify_data["app_id"] == fb_keys["app"])):
            user = get_settings(compID, instance)
            if user and user.access_token_data:
                access_token_data = loads(user.access_token_data)
                if not access_token_data["user_id"] == verify_data["user_id"]:
                  return "Invalid Access Token"
            graph = facebook.GraphAPI(short_token)
            long_token = graph.extend_access_token(fb_keys["app"], \
                                             fb_keys["secret"])
            long_token["generated_time"] = str(int(time()))
            long_token["user_id"] = verify_data["user_id"]
            return long_token
    except facebook.GraphAPIError, e:
        print e.message
        return "Facebook Error"

until_regex = compile("until=([0-9]+)")
after_regex = compile("after=([0-9A-Za-z=]+)")

def get_event_data(events_info, access_token_data):
    """This function gets all of the data of the events created by the user on
    Facebook that the user wants to display on her calendar or list. It is used
    on every load of the widget.
    """
    data = get_event_info("", access_token_data["access_token"], len(events_info))
    if (data) or (data == []):
        return process_event_data(events_info, data, access_token_data["access_token"])
    else:
        return False

def get_event_info(since, access_token, events_length):
    """This function gets all the event data of the user from Facebook, but it
    only gets data for events that started "since" seconds ago. When "since" is
    not provided, it gets as many event as possible. 

    But regardless of since, only a max of 100 events are retrieved at a time.
    100 is an artificial barrier for performance sake and not to overwhelm the
    user in the settings panel, but can be increased or decreased as desired.

    The function works by paging through the event data from Facebook and
    storing it all in "final_event_data". Because Facebook's data is across
    multiple pages, the while loop is used. 
    """
    final_event_data = [];
    next_page = True;
    graph = facebook.GraphAPI(access_token)
    after = ""
    until = ""
    while(next_page):
        events = graph.get_object("/me/events/created", since=since, after=after, until=until)
        try:
            final_event_data += events["data"]
            if (not since) and len(final_event_data) > 100 and len(final_event_data) > (events_length * 2):
                next_page = False
            if events["paging"]:
                if "cursors" in events["paging"]:
                    if "after" in events["paging"]["cursors"]:
                        after = events["paging"]["cursors"]["after"]
                        until = ""
                    else:
                        next_page = False
                else:
                    next = events["paging"]["next"]
                    until_pattern = until_regex.search(next)
                    if until_pattern is None:
                        after_pattern = after_regex.search(next)
                        if after_pattern is None:
                            raise Exception("Regex is not working")
                        else:
                            after = after_pattern.group(1)
                            until = ""
                    else:
                        until = until_pattern.group(1)
                        after = ""
        except KeyError, e:
            next_page = False
            return final_event_data
        except facebook.GraphAPIError, e:
            print "FACEBOOK ERROR " + e.message
            next_page = False
            return False
        except Exception, e:
            print "ERROR " + e.message
            next_page = False
            return final_event_data
    return final_event_data

def process_event_data(events_info, event_data, access_token):
    """This function processes all the event data from Facebook.
    
    For the safety of the user, location data is removed from all events. Since
    this data is only used in the widget, location data isn't necessary
    anyways.

    In addition, all events that are not on the list of events that the user
    wants on her calendar or list are removed here.

    Lastly, if there are any events on the user's list that we failed to get the
    data for already, we make sure to get that event data by calling
    get_specific_event.
    """
    processed_events = []
    for saved_event in events_info:
        cur_event_data = next((event for event in event_data if event["id"] == saved_event["eventId"]), None)
        if cur_event_data is None:
            cur_event_data = get_specific_event(saved_event["eventId"], access_token, "all")
        if cur_event_data:
            cur_event_data = clean_data_dict(cur_event_data)
            cur_event_data["location"] = ""
            cur_event_data["venue"] = ""
            cur_event_data["eventColor"] = saved_event["eventColor"]
            processed_events.append(cur_event_data)
    return processed_events

def get_specific_event(eventId, access_token, desired_data):
    """This function gets all the desired data for a specific event.

    This function can get all the basic data for an event as well as get the
    cover photo, guest stats, and feed for an event.

    This function is used primarily by the modal, but also sometimes by the
    widget if an event's data could not be retrieved in the mass retrieval
    process.
    """
    try:
        url = "/" + eventId
        graph = facebook.GraphAPI(access_token)
        if desired_data == "cover":
            data = graph.get_object(url, fields="cover")
        elif desired_data == "guests":
            query = "SELECT attending_count, unsure_count, not_replied_count from event WHERE eid = " + eventId
            data = graph.get_object("/fql", q=query)
        elif desired_data == 'feed':
            data = graph.get_object(url + "/feed")
        else:
            data = graph.get_object(url)
        data = clean_data_dict(data)
        return data
    except facebook.GraphAPIError, e:
        print "FACEBOOK ERROR " + e.message
        return {}

def clean_data_dict(data):
    """This function removes the access token from all data returned from
    Facebook. 

    It is used by the modal when retrieving data on an event.

    This function exists primarily for security reasons. When Facebook returns
    data that is across multiple pages, it also returns paging links to get to
    the data on the next/previous page. In these links, the access token is
    displayed. Since these links are passed to the client side, it is imperative
    that the access token be removed from these links for the safety of the
    user.

    All data that is passed to the client side is run through this function.
    """
    if type(data) is dict:
        for key in data:
            if type(data[key]) is dict:
                data[key] = clean_data_dict(data[key])
            elif type(data[key]) is list:
                data[key] = clean_data_list(data[key])
            elif type(data[key]) is unicode:
                data[key] = sub(r"access_token=[0-9A-Za-z]+", "", data[key])
    return data

def clean_data_list(data):
    """This is a helper function for the clean_data_dict function. It processes
    lists while the original processes dictionaries.
    """
    if type(data) is list:
        for index in range(0, len(data)):
            if type(data[index]) is list:
                data[index] = clean_data_list(data[index])
            elif type(data[index]) is dict:
                data[index] = clean_data_dict(data[index])
            elif type(data[index]) is unicode:
                data[index] = sub(r"access_token=[0-9A-Za-z]+", "", data[index])
    return data

def get_all_event_data(access_token_data):
    """This function gets all the event data for the user going back till events
    starting three months ago.

    This timeframe can be increased or decreased depending on the developer's
    preference and best judgement.

    This function is only used by the settings panel and only used when getting
    event data on the client side fails.
    """

    try:
        cur_time = int(time())
        seconds_in_three_months = 60 * 60 * 24 * 90
        time_three_months_ago = str(cur_time - seconds_in_three_months)
        event_info = get_event_info(time_three_months_ago,
                              access_token_data["access_token"], 0)
        for i in range(0, len(event_info)):
            event_info[i] = clean_data_dict(event_info[i])
        return event_info
    except facebook.GraphAPIError, e:
        print e.message
        return False

def get_user_name(access_token_data):
    """This function gets the name of the user. It is used by the settings panel
    to show whose Facebook account is logged into the app.
    """
    try:
        graph = facebook.GraphAPI(access_token_data["access_token"])
        me = graph.get_object("/me")
        name = me["name"]
        return name
    except facebook.GraphAPIError, e:
        print e.message
        return ""

def get_more_feed(object_id, access_token, desired_data, after, until):
    """This function is used by the modal to get more status on an event feed
    or more comments on a status. It utilizes paging tokens parsed on the
    client side and passed to the server. (This is also a reason why the
    clean_data_dict function doesn't just simply remove the paging data.)

    The reason they are parsed on the client side and not the server is because
    the server is stateless and does not remember paging tokens nor store them
    anywhere, so the client must be specific about what data it is requesting.
    """
    try:
        graph = facebook.GraphAPI(access_token)
        if after:
            feed = graph.get_object("/" + object_id + "/" + desired_data, after = after)
        else:
            feed = graph.get_object("/" + object_id + "/" + desired_data, until = until)
        return feed
    except facebook.GraphAPIError, e:
        print e.message
        return {}
