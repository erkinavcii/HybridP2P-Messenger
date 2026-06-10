import flet as ft
import asyncio
import threading

def main(page: ft.Page):
    fp = ft.FilePicker()
    
    # In Flet 0.25, maybe FilePicker is added to page.overlay?
    # Let's test if page.overlay.append causes Unknown Control in console
    page.overlay.append(fp)
    page.update()
    print("Overlay updated")
    
    # Test if we can pick files
    def btn_click(e):
        print("Pick files clicked")
        # In Flet 0.85, maybe pick_files is async but we are in a sync handler?
        # we can't await here.
    
    page.add(ft.ElevatedButton("Pick", on_click=btn_click))

# Stop after 2 seconds
def stop_app():
    import time
    time.sleep(2)
    import os
    os._exit(0)

threading.Thread(target=stop_app).start()
ft.app(target=main)
