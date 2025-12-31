import asyncio
import json
import re
import datetime
import kroger_cli.cli
from kroger_cli.memoize import memoized
from kroger_cli import helper
import zendriver as zd


class KrogerAPI:
    # zendriver configuration
    headless = False
    user_data_dir = '.user-data'

    def __init__(self, cli):
        self.cli: kroger_cli.cli.KrogerCLI = cli

    def complete_survey(self):
        # Cannot use headless mode here for some reason (sign-in cookie doesn't stick)
        self.headless = False
        res = asyncio.get_event_loop().run_until_complete(self._complete_survey())
        self.headless = True

        return res

    @memoized
    def get_account_info(self):
        return asyncio.get_event_loop().run_until_complete(self._get_account_info())

    @memoized
    def get_points_balance(self):
        return asyncio.get_event_loop().run_until_complete(self._get_points_balance())

    def clip_coupons(self):
        return asyncio.get_event_loop().run_until_complete(self._clip_coupons())

    @memoized
    def get_purchases_summary(self):
        return asyncio.get_event_loop().run_until_complete(self._get_purchases_summary())

    async def _retrieve_feedback_url(self):
        self.cli.console.print('Loading `My Purchases` page (to retrieve the Feedback\'s Entry ID)')

        # Modal overlay pop up (might not exist)
        # Need to click on it, as it prevents me from clicking on `Order Details` link
        try:
            overlay = await self.page.select('.ModalitySelectorDynamicTooltip--Overlay')
            if overlay:
                await overlay.click()
        except Exception:
            pass

        try:
            # `See Order Details` link
            await self.page.wait(2)
            details_btn = await self.page.select('.PurchaseCard-top-view-details-button a')
            if details_btn:
                await details_btn.click()
                await self.page.wait(2)

            # `View Receipt` link
            receipt_btn = await self.page.select('.PurchaseCard-top-view-details-button a')
            if receipt_btn:
                await receipt_btn.click()
                await self.page.wait(2)

            content = await self.page.get_content()
        except Exception:
            link = 'https://www.' + self.cli.config['main']['domain'] + '/mypurchases'
            self.cli.console.print('[bold red]Couldn\'t retrieve the latest purchase, please make sure it exists: '
                                   '[link=' + link + ']' + link + '[/link][/bold red]')
            raise Exception

        try:
            match = re.search('Entry ID: (.*?) ', content)
            entry_id = match[1]
            match = re.search('Date: (.*?) ', content)
            entry_date = match[1]
            match = re.search('Time: (.*?) ', content)
            entry_time = match[1]
            self.cli.console.print('Entry ID retrieved: ' + entry_id)
        except Exception:
            current_url = self.page.url if hasattr(self.page, 'url') else 'unknown'
            self.cli.console.print('[bold red]Couldn\'t retrieve Entry ID from the receipt, please make sure it exists: '
                                   '[link=' + current_url + ']' + current_url + '[/link][/bold red]')
            raise Exception

        entry = entry_id.split('-')
        hour = entry_time[0:2]
        minute = entry_time[3:5]
        meridian = entry_time[5:7].upper()
        date = datetime.datetime.strptime(entry_date, '%m/%d/%y')
        full_date = date.strftime('%m/%d/%Y')
        month = date.strftime('%m')
        day = date.strftime('%d')
        year = date.strftime('%Y')

        url = f'https://www.krogerstoresfeedback.com/Index.aspx?' \
              f'CN1={entry[0]}&CN2={entry[1]}&CN3={entry[2]}&CN4={entry[3]}&CN5={entry[4]}&CN6={entry[5]}&' \
              f'Index_VisitDateDatePicker={month}%2f{day}%2f{year}&' \
              f'InputHour={hour}&InputMeridian={meridian}&InputMinute={minute}'

        return url, full_date

    async def _complete_survey(self):
        signed_in = await self.sign_in_routine(redirect_url='/mypurchases', contains=['My Purchases'])
        if not signed_in:
            await self.destroy()
            return None

        try:
            url, survey_date = await self._retrieve_feedback_url()
        except Exception:
            await self.destroy()
            return None

        # Navigate to survey page
        self.page = await self.browser.get(url)
        await self.page
        await self.page.wait(3)

        # Wait for date picker and set the date
        try:
            date_picker = await self.page.select('#Index_VisitDateDatePicker')
            if date_picker:
                # We need to manually set the date, otherwise the validation fails
                js = "() => {$('#Index_VisitDateDatePicker').datepicker('setDate', '" + survey_date + "');}"
                await self.page.evaluate(js)

            next_btn = await self.page.select('#NextButton')
            if next_btn:
                await next_btn.click()
        except Exception:
            pass

        for i in range(35):
            await self.page.wait(2)
            current_url = self.page.url if hasattr(self.page, 'url') else ''

            try:
                next_btn = await self.page.select('#NextButton')
                if not next_btn:
                    if 'Finish' in current_url:
                        await self.destroy()
                        return True
                    continue

                await self.page.evaluate(helper.get_survey_injection_js(self.cli.config))
                await next_btn.click()
            except Exception:
                if 'Finish' in current_url:
                    await self.destroy()
                    return True

        await self.destroy()
        return False

    async def _get_account_info(self):
        # Sign in and redirect to account/update page which has profile info
        signed_in = await self.sign_in_routine(redirect_url='/account/update', contains=['Profile Information'])
        if not signed_in:
            await self.destroy()
            return None

        self.cli.console.print('Loading profile info..')
        await self.page.wait(2)

        profile = {}
        try:
            # Scrape profile data from the page using data-qa selectors
            email_elem = await self.page.find('[data-qa="Current Email: -value"]')
            if email_elem:
                profile['emailAddress'] = email_elem.text

            card_elem = await self.page.find('[data-qa="Current Value Card Number: -value"]')
            if card_elem:
                profile['loyaltyCardNumber'] = card_elem.text

            alt_id_elem = await self.page.find('[data-qa="Current Alt ID: -value"]')
            if alt_id_elem:
                profile['alternateId'] = alt_id_elem.text

        except Exception as e:
            print("EXCEPTION")
            print(e)
            profile = None

        await self.destroy()
        return profile

    async def _get_points_balance(self):
        signed_in = await self.sign_in_routine()
        if not signed_in:
            await self.destroy()
            return None

        self.cli.console.print('Loading points balance..')
        self.page = await self.browser.get('https://www.' + self.cli.config['main']['domain'] + '/accountmanagement/api/points-summary')
        await self.page
        try:
            content = await self.page.get_content()
            balance = self._get_json_from_page_content(content)
            program_balance = balance[0]['programBalance']['balance']
        except Exception:
            balance = None
        await self.destroy()

        return balance

    async def _clip_coupons(self):
        signed_in = await self.sign_in_routine(redirect_url='/cl/coupons/', contains=['Coupons Clipped'])
        if not signed_in:
            await self.destroy()
            return None

        js = """
            window.scrollTo(0, document.body.scrollHeight);
            for (let i = 0; i < 150; i++) {
                let el = document.getElementsByClassName('kds-Button--favorable')[i];
                if (el !== undefined) {
                    el.scrollIntoView();
                    el.click();
                }
            }
        """

        self.cli.console.print('[italic]Applying the coupons, please wait..[/italic]')

        # Dismiss any popup by pressing Escape
        try:
            body = await self.page.select('body')
            if body:
                await body.send_keys(zd.SpecialKeys.ESCAPE)
        except Exception:
            pass

        for i in range(6):
            await self.page.evaluate(js)
            await self.page.scroll_down(500)
            await self.page.wait(1)
        await self.page.wait(3)
        await self.destroy()
        self.cli.console.print('[bold]Coupons successfully clipped to your account! :thumbs_up:[/bold]')

    async def _get_purchases_summary(self):
        signed_in = await self.sign_in_routine()
        if not signed_in:
            await self.destroy()
            return None

        self.cli.console.print('Loading your purchases..')
        self.page = await self.browser.get('https://www.' + self.cli.config['main']['domain'] + '/mypurchases/api/v1/receipt/summary/by-user-id')
        await self.page
        try:
            content = await self.page.get_content()
            data = self._get_json_from_page_content(content)
        except Exception:
            data = None
        await self.destroy()

        return data

    async def init(self):
        # Start browser with user_data_dir for cookie persistence
        self.browser = await zd.start(
            headless=self.headless,
            user_data_dir=self.user_data_dir
        )
        self.page = None  # Page is created on first navigation

    async def destroy(self):
        if self.browser:
            await self.browser.stop()
            # Allow Chrome to save profile/cookie data before exiting
            import asyncio
            await asyncio.sleep(1)

    async def sign_in_routine(self, redirect_url='/account/update', contains=None):
        if contains is None and redirect_url == '/account/update':
            contains = ['Profile Information']

        await self.init()
        self.cli.console.print('[italic]Signing in.. (please wait, it might take awhile)[/italic]')
        signed_in = await self.sign_in(redirect_url, contains)

        if not signed_in and self.headless:
            self.cli.console.print('[red]Sign in failed. Trying one more time..[/red]')
            self.headless = False
            await self.destroy()
            await self.init()
            signed_in = await self.sign_in(redirect_url, contains)

        if not signed_in:
            self.cli.console.print('[bold red]Sign in failed. Please make sure the username/password is correct.'
                                   '[/bold red]')

        return signed_in

    async def sign_in(self, redirect_url, contains):
        timeout = 20 if self.headless else 10  # seconds

        # Navigate to sign-in page
        sign_in_url = 'https://www.' + self.cli.config['main']['domain'] + '/signin?redirectUrl=' + redirect_url
        self.page = await self.browser.get(sign_in_url)
        await self.page
        await self.page.wait(2)

        try:
            # Dismiss any popups that may appear
            try:
                dismissbtn = await self.page.find('Dismiss', timeout=3)
                if dismissbtn:
                    await dismissbtn.click()
            except Exception:
                pass

            try:
                closepop = await self.page.find('Close pop-up', timeout=2)
                if closepop:
                    await closepop.click()
            except Exception:
                pass

            # Find and fill email field
            email_field = await self.page.find('signInName')
            print(email_field)
            if email_field:
                await email_field.click()
                await email_field.clear_input()
                await email_field.send_keys(self.cli.username)

            # Find and fill password field
            password_field = await self.page.find('password')
            if password_field:
                await password_field.click()
                await password_field.clear_input()
                await password_field.send_keys(self.cli.password)
                await password_field.send_keys(zd.SpecialKeys.ENTER)

            # Wait for navigation/login to complete
            await self.page.wait(timeout)

        except Exception:
            return False

        # Verify login success by checking page content
        if contains is not None:
            try:
                content = await self.page.get_content()
                for item in contains:
                    if item not in content:
                        return False
            except Exception:
                return False

        return True

    def _get_json_from_page_content(self, content):
        match = re.search('<pre.*?>(.*?)</pre>', content)
        return json.loads(match[1])
