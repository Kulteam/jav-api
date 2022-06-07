import asyncio
import os
import re
from typing import Optional

from httpx import AsyncClient
from lxml import html
from redis import asyncio as aioredis

BASE_URL = 'https://www.boobpedia.com'


async def boobpedia_special_query(client: AsyncClient, name: str) -> str:
    """Fulltext search on boobpedia"""
    # this is a search page scrap
    params = {'title': 'Special:Search',
              'search': name,
              'profile': 'default',
              'fulltext': '1'}
    tree = await get_page_content(client, '/wiki/index.php', params=params)
    try:
        for results in tree.findall('.//li[@class="mw-search-result"]'):
            # In search result...
            if results.find('div/a').get('title') == name:
                return results.find('div/a').get('href')
            if bool(re.search('alias =', results.find('div[2]').text)):
                # ... Check which search result has 'alias =' pattern ..
                return results.find('div[1]/a').get('href')
                # ... return href value from that search result
    except TypeError:
        return None


async def get_page_content(client: AsyncClient, url: str, **kwargs) -> html.HtmlElement:
    """Get the page content as bytes, takes url as optional argument"""
    return html.fromstring((await client.get(url, **kwargs)).content)


async def parse_actress_details(tree: html.HtmlElement, name: Optional[str] = None) -> dict | None:
    """Parse actress details from given page (also search in database for r18 actress links)"""

    details = {}
    details['name'] = tree.findall('.//h1[@class="firstHeading"]')[0].text
    if details['name'] is not None:
        details['name'] = details['name'].strip()
        if bool(re.search('(disambiguation)', details['name'])):
            pass
        elif details['name'] is not bool(re.search('(disambiguation)', details['name'])):
            try:
                try:
                    details['image'] = BASE_URL + '/'.join(tree.findall
                                                           (f'.//a[@title="{details["name"]}"]/img')[
                                                               0]
                                                           .get('src').replace('/thumb', '').split('/')[0:-1])
                except IndexError:
                    details['image'] = BASE_URL + '/'.join(tree.findall
                                                           ('.//td[@colspan="2"]/a/img')[0]
                                                           .get('src').replace('/thumb', '').split('/')[0:-1])

                r18_image = await r18_database(details['name'])
                if r18_image is not None:
                    details['image2'] = r18_image

                # Test 3 passed
                for result in tree.findall('.//tr[@valign="top"]'):
                    match = result.find('td/b').text.strip()
                    if bool(re.search('Also', match)):
                        details['also_known_as'] = result.find(
                            'td[2]').text.strip()
                    elif bool(re.search('Born', match)):
                        details['born'] = result.find(
                            'td/span/span[@class="bday"]').text.strip()
                    elif bool(re.search('Measurements', match)):
                        details['measurements'] = result.find(
                            'td[2]').text.strip()
                    elif bool(re.search('Bra', match)):
                        details['cup_size'] = result.find(
                            'td/a').text.replace(' metric', '').strip()
                    elif bool(re.search('Boobs', match)):
                        details['boob_type'] = result.find('td/a').text.strip()

                # Social Media Links / External Links
                for result in tree.findall('.//tr/td/b/a[@class="external text"]'):
                    details[result.text.strip().lower()] = result.get('href')

                return details
            # If line 70 raises an IndexError , then details['name'] is not an actress's name
            except IndexError:
                pass
        if len(details) < 2 and name is not None:
            r18_image = await r18_database(details['name'])
            if r18_image is not None:
                details['name'] = name
                details['image'] = r18_image
                return details


async def r18_database(name: str) -> (str | None):
    """Search from redis-r18 database"""
    async with aioredis.Redis.from_url(os.environ.get('REDIS_URL'),
                                       decode_responses=True,
                                       db=0) as redis:
        tasks = []
        name = list(name.split(' '))
        # case 1: name is a single word
        if len(name) == 1:
            name = name[0]  # selecting 1st word
            tasks.append(asyncio.create_task(redis.get(name)))

            # case 1.2: name is full captalized
            tasks.append(asyncio.create_task(redis.get(name.upper())))
        # case 2: name is a multi-word
        elif len(name) > 1:
            for pos, alias in enumerate(name):
                name[pos] = alias.capitalize()
            tasks.append(asyncio.create_task(redis.get(' '.join(name))))

            # case 2.2: some names have surname before name
            name.reverse()
            tasks.append(asyncio.create_task(redis.get(' '.join(name))))

        for result in await asyncio.gather(*tasks):
            if result is not None:
                return result


async def actress_handler(client: AsyncClient, actress_name: str) -> dict[str] | None:
    """Actress Handle Function"""
    actress_search_task = []
    actress_search_task.append(asyncio.create_task(
        boobpedia_special_query(client, actress_name), name='boobpedia_query'))
    actress_search_task.append(asyncio.create_task(
        r18_database(actress_name), name='r18_database_result'))

    for pos, result in enumerate(await asyncio.gather(*actress_search_task)):
        if result is not None and pos == 0:
            tree = await get_page_content(client, result)
            return await parse_actress_details(tree, actress_name)
        if result is not None and pos == 1:
            return {'name': actress_name, 'image': result}


async def actress_search(actress_list: list[str], only_r18: bool = False) -> list[dict]:
    """Takes actress name list (raw) as input, return a dictionary filled with details [boobpedia + r18]"""
    actress_details = []
    boobpedia_search_task = []
    async with AsyncClient(base_url=BASE_URL, http2=True, follow_redirects=True, timeout=None) as client:
        for actress_name in actress_list:
            # Convertion of single word name to Full capitalized name
            # if len(actress_name.split(' ')) == 1:
            #     actress_name = actress_name.upper()

            if not only_r18:
                boobpedia_search_task.append(asyncio.create_task(
                    actress_handler(client, actress_name)))
            else:
                actress_url = await r18_database(actress_name)
                if actress_url is not None:
                    actress_details.append({'name': actress_name, 'image': actress_url})

        if not only_r18:
            results = await asyncio.gather(*boobpedia_search_task)
            for result in results:
                if result is not None and \
                        result['name'] not in set(actress['name'] for actress in actress_details):
                    actress_details.append(result)
        return actress_details

if __name__ == '__main__':
    import json
    print(json.dumps(asyncio.run(actress_search(
        ['Ema Kisaki'], only_r18=False)), indent=2, ensure_ascii=False))
    # # with open('./boobpedia_search_result.html', 'rb') as _input:
    #     tree = html.fromstring(_input.read())
    # print(json.dumps(asyncio.run(actress_search(['Ema Kisaki'])), indent=2, ensure_ascii=False))