#!/usr/bin/env python3

"""Copyright (c) 2021, Douglas Otwell
   Ported to Gtk3 and modified by P.I.Julius

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import gi
import sys
import time
import logging
import threading
import queue
import dbus
from dbus.mainloop.glib import DBusGMainLoop
gi.require_version('Gtk', '3.0')
from gi.repository import GObject, Gtk, Gdk, GLib, Pango

DBUS_OM_IFACE =         "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE =       "org.freedesktop.DBus.Properties"
BLUEZ_SERVICE_NAME =    "org.bluez"
MEDIA_PLAYER_IFACE =    BLUEZ_SERVICE_NAME + ".MediaPlayer1"
MEDIA_TRANSPORT_IFACE = BLUEZ_SERVICE_NAME + ".MediaTransport1"
MEDIA_CONTROL_IFACE =   BLUEZ_SERVICE_NAME + ".MediaControl1" # deprecated
MEDIA_DEVICE_IFACE =    BLUEZ_SERVICE_NAME + ".Device1"

CSS = """
    window {
        background: #5294e2;
    }
    grid {
        padding: 5px 10px 5px 10px;
    }
    #playButton {
        padding: 1px 10px;
    }
    """

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
LOG_LEVEL =  logging.DEBUG

bt = None
mediaPlayer = None
mediaTransport = None

def getInterface(bus, iface):
    global mediaPlayer

    objects = []
    om = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, "/"), DBUS_OM_IFACE)
    objs = om.GetManagedObjects()

    for obj, props in objs.items():
        if iface in props:
            objects.append(obj)

    if len(objects) == 0:
        return None

    if len(objects) == 1:
        return objects[0]

    if mediaPlayer:
        path = mediaPlayer.object_path[:-7]

        for obj in objects:
            if path in obj:
                return obj

    logging.error("Multiple objects found for interface: {}".format(iface))

def getPlayerAndTransport():
    global mediaPlayer, mediaTransport

    if not mediaPlayer:
        path = getInterface(bus, MEDIA_PLAYER_IFACE)

        if not path:
            return (None, None)

        logging.info("Found a media player at {}".format(path))
        mediaPlayer = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, path), dbus_interface=MEDIA_PLAYER_IFACE)

    if not mediaTransport:
        path = getInterface(bus, MEDIA_TRANSPORT_IFACE)

        if not path:
            return (None, None)

        logging.info("Found a media transport at {}".format(path))
        mediaTransport = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, path), dbus_interface=MEDIA_TRANSPORT_IFACE)

    return (mediaPlayer, mediaTransport)

def handlePipeline():
    global bt, mediaPlayer, mediaTransport

    if not mediaPlayer:
        mediaPlayer, mediaTransport = getPlayerAndTransport()

        if mediaPlayer:
            props = mediaPlayer.GetAll(MEDIA_PLAYER_IFACE, dbus_interface=DBUS_PROP_IFACE)

            if "Status" in props:
                if props["Status"] == "playing":
                    bt.setPlayPause(True)
                else:
                    bt.setPlayPause(False)

            if "Track" in props:
                bt.setTrack(props["Track"])

            bt.ready()

    else:
        while not pipeline.empty():
            item = pipeline.get()

            if MEDIA_PLAYER_IFACE in item:
                props = item[MEDIA_PLAYER_IFACE]

                if "Track" in props:
                    logging.info("Updating track")
                    bt.setTrack(props["Track"])

                if "Status" in props:
                    logging.debug("Media player Status: {}".format(props["Status"]))
                    if props["Status"] == "playing":
                        bt.setPlayPause(True)
                    else:
                        bt.setPlayPause(False)

                if "Position" in props:
                    #logging.debug("Media player Position: {}".format(props["Position"]))
                    pass

            elif MEDIA_TRANSPORT_IFACE in item:
                props = item[MEDIA_TRANSPORT_IFACE]

                if "State" in props:
                    logging.debug("Media transport State: {}".format(props["State"]))
                    if props["State"] == "idle":
                        bt.setPlayPause(False)
                    if props["State"] == "active":
                        bt.setPlayPause(True)

                if "Volume" in props:
                    logging.debug("Media transport Volume: {}".format(props["Volume"]))

            elif MEDIA_DEVICE_IFACE in item:
                props = item[MEDIA_DEVICE_IFACE]
                if "Connected" in props:
                    if props["Connected"]:
                        logging.info("The remote device connected")
                        bt.show()
                    else:
                        logging.info("The remote device disconnected")
                        mediaPlayer = None
                        mediaTransport = None
                        bt.hide()

            elif MEDIA_CONTROL_IFACE in item:
                logging.debug("Media control properties changed")

            else:
                logging.warning("Missing information from pipeline: {}".format(item))

    return True

class BlueListener(threading.Thread):
    pipeline = None

    def __init__(self, pipe):
        self.pipeline = pipe
        threading.Thread.__init__(self, daemon=True)

    def _propsChangedCb(self, iface, changed, invalidated):
        self.pipeline.put({iface: changed})

    def run(self):
        receiver = bus.add_signal_receiver(
                self._propsChangedCb,
                bus_name = BLUEZ_SERVICE_NAME,
                dbus_interface = DBUS_PROP_IFACE,
                signal_name = "PropertiesChanged")

class BlueTunes(Gtk.Window):
    global bt, mediaPlayer, mediaTransport

    def __init__(self):
        Gtk.Window.__init__(self, title="BlueTunes")

        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-application-prefer-dark-theme", True)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CSS.encode())
        context = Gtk.StyleContext()
        screen = Gdk.Screen.get_default()
        context.add_provider_for_screen(screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.connect('destroy', self.quit)
        self.set_default_size(600, 40)

        grid = Gtk.Grid()

        prevImg = Gtk.Image.new_from_icon_name("media-skip-backward-symbolic", Gtk.IconSize.BUTTON)
        nextImg = Gtk.Image.new_from_icon_name("media-skip-forward-symbolic", Gtk.IconSize.BUTTON)

        self.playImg = Gtk.Image.new_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.BUTTON)
        self.pauseImg = Gtk.Image.new_from_icon_name("media-playback-pause-symbolic", Gtk.IconSize.BUTTON)

        playPauseBox = Gtk.Box()
        playPauseBox.set_halign(Gtk.Align.CENTER)
        playPauseBox.add(self.playImg)
        playPauseBox.add(self.pauseImg)

        self.loadingLabel = Gtk.Label(label="Waiting for devices to connect")
        self.loadingLabel.set_ellipsize(Pango.EllipsizeMode.END)
        self.loadingLabel.set_hexpand(True)
        self.loadingLabel.set_halign(Gtk.Align.START)

        self.trackLabel = Gtk.Label(label="")
        self.trackLabel.set_ellipsize(Pango.EllipsizeMode.END)
        self.trackLabel.set_hexpand(True)
        self.trackLabel.set_halign(Gtk.Align.START)

        self.prevBtn = Gtk.Button()
        self.prevBtn.set_image(prevImg)
        self.prevBtn.connect("clicked", self.prev)

        self.playBtn = Gtk.Button()
        self.playBtn.set_name("playButton")
        self.playBtn.add(playPauseBox)
        self.playBtn.connect("clicked", self.play)

        self.nextBtn = Gtk.Button()
        self.nextBtn.set_image(nextImg)
        self.nextBtn.connect("clicked", self.next)

        grid.add(self.loadingLabel)
        grid.add(self.trackLabel)
        grid.add(self.prevBtn)
        grid.add(self.playBtn)
        grid.add(self.nextBtn)

        self.add(grid)
        self.show_all()

        self.loading()
        self.pauseImg.hide()

    def setTrack(self, props):
        title = props["Title"] if "Title" in props else None
        artist = props["Artist"] if "Artist" in props else None
        album = props["Album"] if "Album" in props else None

        self.trackLabel.set_markup(
                "<b>"+GLib.markup_escape_text(artist)+"</b> " +
                GLib.markup_escape_text(title)+" " +
                GLib.markup_escape_text(album))

    def setPlayPause(self, playing):
        if playing:
            self.playImg.hide()
            self.pauseImg.show()
        else:
            self.pauseImg.hide()
            self.playImg.show()

    def ready(self):
        self.prevBtn.set_sensitive(True)
        self.playBtn.set_sensitive(True)
        self.nextBtn.set_sensitive(True)

        self.loadingLabel.hide()
        self.trackLabel.show()

    def loading(self):
        self.prevBtn.set_sensitive(False)
        self.playBtn.set_sensitive(False)
        self.nextBtn.set_sensitive(False)

        self.trackLabel.hide()
        self.loadingLabel.show()

    def play(self, window):
        status = mediaPlayer.Get(MEDIA_PLAYER_IFACE, "Status", dbus_interface=DBUS_PROP_IFACE)

        if status == "playing":
            mediaPlayer.Pause()
        else:
            mediaPlayer.Play()

    def stop(self, window):
        mediaPlayer.Stop()

    def next(self, window):
        mediaPlayer.Next()

    def prev(self, window):
        mediaPlayer.Previous()

    def volUp(self, window):
        vol = mediaTransport.Get(MEDIA_TRANSPORT_IFACE, "Volume", dbus_interface=DBUS_PROP_IFACE)
        vol = vol + 4 if vol < 124 else 127

        mediaTransport.Set(MEDIA_TRANSPORT_IFACE, "Volume", dbus.UInt16(vol), dbus_interface=DBUS_PROP_IFACE)

    def volDown(self, window):
        vol = mediaTransport.Get(MEDIA_TRANSPORT_IFACE, "Volume", dbus_interface=DBUS_PROP_IFACE)
        vol = vol - 4 if vol > 4 else 0

        mediaTransport.Set(MEDIA_TRANSPORT_IFACE, "Volume", dbus.UInt16(vol), dbus_interface=DBUS_PROP_IFACE)

    def run(self):
        handlePipeline()
        GLib.timeout_add(1000, handlePipeline)
        Gtk.main()

    def quit(self, window):
        Gtk.main_quit()

#
#  Main
#

logging.basicConfig(stream=sys.stdout, format=LOG_FORMAT, level=LOG_LEVEL)
DBusGMainLoop(set_as_default=True)

logging.info("Starting BlueTunes media controller")
pipeline = queue.SimpleQueue()
bus = dbus.SystemBus()

logging.debug("Starting Bluetooth listener")
listener = BlueListener(pipeline)
listener.start()

logging.debug("Starting BlueTunes main loop")
bt = BlueTunes()
bt.run()

logging.info("Exiting BlueTunes media controller")
