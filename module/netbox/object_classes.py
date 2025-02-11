# -*- coding: utf-8 -*-
#  Copyright (c) 2020 - 2021 Ricardo Bartels. All rights reserved.
#
#  netbox-sync.py
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see file LICENSE.txt included in this
#  repository or visit: <https://opensource.org/licenses/MIT>.

import json
from ipaddress import ip_network, IPv4Network, IPv6Network

from module.common.misc import grab, do_error_exit
from module.common.logging import get_logger

log = get_logger()


class NetBoxObject:
    """
    Base class for all NetBox object types. Implements all methods used on a NetBox object.

    Sub classes need to have following attributes:
        name:
            name of the object type (i.e. "virtual machine")
        api_path:
            NetBox api path of object type (i.e: "virtualization/virtual-machines")
        primary_key:
            name of the data model key which represents the primary key of this object besides id (i.e: "name")
        data_model:
            dict of permitted data keys and possible values (see description below)

    optional attributes
        secondary_key:
            name of the data model key which represents the secondary key of this object besides id
        enforce_secondary_key:
            bool if secondary key of an object shall be added to name when get_display_name() method is called

    The data_model attribute needs to be a dict describing the data model in NetBox.
    Key must be string.
    Value can be following types:
        int (instance):
            value of this attribute must be a string and will be truncated if string exceeds max length of "int"
        int (class):
            value must be an integer
        str (class):
            can be a string with an undefined length
        bool (class):
            attribute must be True or False
        NetBoxObject sub class:
            value of this key is a reference to another NetBoxObject of exact defined type
        list (instance):
            value can be one of the predefined values in that list
        list of NetBoxObject sub classes:
            value must be an instance of predefined netBoxObject classes in list
        NBObjectList sub class:
            value mast be the defined sub class of NBObjectList


    """

    # list of default attributes which are added to every netbox object during init
    default_attributes = {
        "data": None,
        "is_new": True,
        "nb_id": 0,
        "updated_items": list(),
        "unset_items": list(),
        "source": None,
    }

    # keep handle to inventory instance to append objects on demand
    inventory = None

    def __init__(self, data=None, read_from_netbox=False, inventory=None, source=None):

        # inherit and create default attributes from parent
        for attr_key, attr_value in self.default_attributes.items():
            if isinstance(attr_value, (list, dict, set)):
                setattr(self, attr_key, attr_value.copy())
            else:
                setattr(self, attr_key, attr_value)

        # store provided inventory handle
        self.inventory = inventory

        # initialize empty data dict
        self.data = dict()

        # add empty lists for list items
        for key, data_type in self.data_model.items():
            if data_type in NBObjectList.__subclasses__():
                self.data[key] = data_type()

        # add data to this object
        self.update(data=data, read_from_netbox=read_from_netbox, source=source)

    def __repr__(self):
        return "<%s instance '%s' at %s>" % (self.__class__.__name__, self.get_display_name(), id(self))

    def to_dict(self):
        """
        returns this object as a dictionary

        Returns
        -------
        dict: dictionary of all relevant items of this object instance
        """

        out = dict()

        for key in dir(self):
            value = getattr(self, key)
            if "__" in key:
                continue
            if callable(value) is True:
                continue
            if key in ["inventory", "default_attributes", "data_model_relation"]:
                continue
            if key == "source":
                value = getattr(value, "name", None)

            if key == "data_model":

                data_model = dict()
                for data_key, data_value in value.items():
                    if isinstance(data_value, list):
                        new_data_value = list()
                        for possible_option in data_value:
                            if type(possible_option) == type:
                                new_data_value.append(str(possible_option))
                            else:
                                new_data_value.append(possible_option)

                        data_value = new_data_value

                    # if value is class name then print class name
                    if type(data_value) == type:
                        data_value = str(data_value)

                    data_model[data_key] = data_value

                value = data_model

            if key == "data":

                data = dict()
                for data_key, data_value in value.items():
                    # if value is class name then print class representation
                    if isinstance(data_value, (NetBoxObject, IPv4Network, IPv6Network)):
                        data_value = repr(data_value)

                    elif isinstance(data_value, NBObjectList):
                        data_value = [repr(x) for x in data_value]

                    data[data_key] = data_value

                value = data

            out[key] = value

        return out

    def __str__(self):
        """
        formats this object as a dict in JSON format

        Returns
        -------
        str: object dict as JSON
        """

        return json.dumps(self.to_dict(), sort_keys=True, indent=4)

    @staticmethod
    def format_slug(text=None, max_len=50):
        """
        Format string to comply to NetBox slug acceptable pattern and max length.

        Parameters
        ----------
        text: str
            name to format into a NetBox slug
        max_len: int
            maximum possible length of slug

        Returns
        -------
        str: input name formatted as slug und truncated if necessary
        """

        if text is None or len(text) == 0:
            raise AttributeError("Argument 'text' can't be None or empty!")

        permitted_chars = (
            "abcdefghijklmnopqrstuvwxyz"  # alphabet
            "0123456789"  # numbers
            "_-"  # symbols
        )

        # Replace separators with dash
        for sep in [" ", ",", "."]:
            text = text.replace(sep, "-")

        # Strip unacceptable characters
        text = "".join([c for c in text.lower() if c in permitted_chars])

        # Enforce max length
        return text[0:max_len]

    # noinspection PyAttributeOutsideInit
    def update(self, data=None, read_from_netbox=False, source=None):
        """
        parse data dictionary and validate input. Add data to object if valid.

        Parameters
        ----------
        data: dict
            dictionary with data to add/update
        read_from_netbox: bool
            True if data was gathered from NetBox via request
        source: source handler
            object handler of source

        Returns
        -------
        None
        """

        if data is None:
            return

        if not isinstance(data, dict):
            raise AttributeError("Argument 'data' needs to be a dict!")

        if data.get("id") is not None:
            self.nb_id = data.get("id")

        if read_from_netbox is True:
            self.is_new = False
            self.data = data
            self.updated_items = list()
            self.unset_items = list()

            return

        if source is not None:
            self.source = source

        display_name = self.get_display_name(data)

        if display_name is None:
            display_name = self.get_display_name()

        log.debug2(f"Parsing '{self.name}' data structure: {display_name}")

        parsed_data = dict()
        for key, value in data.items():

            if key not in self.data_model.keys():
                log.error(f"Found undefined data model key '{key}' for object '{self.__class__.__name__}'")
                continue

            # skip unset values
            if value is None:
                log.info(f"Found unset key '{key}' while parsing {display_name}. Skipping This key")
                continue

            # check data model to see how we have to parse the value
            defined_value_type = self.data_model.get(key)

            #  setting data value for primary_ip here to avoid/circumvent circular dependencies
            if key.startswith("primary_ip"):
                defined_value_type = NBIPAddress

            # value must be a string witch a certain max length
            if isinstance(defined_value_type, int):
                if not isinstance(value, str):
                    log.error(f"Invalid data type for '{self.__class__.__name__}.{key}' (must be str), got: '{value}'")
                    continue

                value = value[0:defined_value_type]

                if key == "slug":
                    value = self.format_slug(text=value, max_len=defined_value_type)
                else:
                    value = value[0:defined_value_type]

            if isinstance(defined_value_type, list):

                if isinstance(value, NetBoxObject):

                    if type(value) not in defined_value_type:
                        log.error(f"Invalid data type for '{key}' (must be one of {defined_value_type}), "
                                  f"got: '{type(value)}'")
                        continue

                # check if value is in defined list
                elif value not in defined_value_type:
                    log.error(f"Invalid data type for '{key}' (must be one of {defined_value_type}), got: '{value}'")
                    continue

            # just check the type of the value
            type_check_failed = False
            for valid_type in [bool, str, int]:

                if defined_value_type == valid_type and not isinstance(value, valid_type):
                    log.error(f"Invalid data type for '{key}' (must be {valid_type.__name__}), got: '{value}'")
                    type_check_failed = True
                    break

            if type_check_failed is True:
                continue

            # tags need to be treated as list of dictionaries, tags are only added
            if defined_value_type == NBTagList:
                value = self.compile_tags(value)

            # VLANs will overwrite the whole list of current VLANs
            if defined_value_type == NBVLANList:
                value = self.compile_vlans(value)

            # this is meant to be reference to a different object
            if defined_value_type in NetBoxObject.__subclasses__():

                if not isinstance(value, NetBoxObject):
                    # try to find object.
                    value = self.inventory.add_update_object(defined_value_type, data=value)
                    # add source if item was created via this source
                    if value.source is None:
                        value.source = source

            # add to parsed data dict
            parsed_data[key] = value

        # add/update slug
        # if data model contains a slug we need to handle it
        if "slug" in self.data_model.keys() and \
                parsed_data.get("slug") is None and \
                parsed_data.get(self.primary_key) is not None:

            parsed_data["slug"] = self.format_slug(text=parsed_data.get(self.primary_key),
                                                   max_len=self.data_model.get("slug"))

        # update all data items
        for key, new_value in parsed_data.items():

            # nothing changed, continue with next key
            current_value = self.data.get(key)
            if current_value == new_value:
                continue

            # get current value str
            if isinstance(current_value, (NetBoxObject, NBObjectList)):
                current_value_str = str(current_value.get_display_name())

            # if data model is a list then we need to read the netbox data value
            elif isinstance(self.data_model.get(key), list) and isinstance(current_value, dict):
                current_value_str = str(current_value.get("value"))

            elif key.startswith("primary_ip") and isinstance(current_value, dict):
                current_value_str = str(current_value.get("address"))

            else:
                current_value_str = str(current_value).replace("\r", "")

            # get new value str
            if isinstance(new_value, (NetBoxObject, NBObjectList)):
                new_value_str = str(new_value.get_display_name())
            else:
                new_value_str = str(new_value).replace("\r", "")

            # support NetBox 2.11+ vcpus float value
            if current_value is not None and \
                    self.data_model.get(key) in [int, float] and \
                    isinstance(new_value, (int, float)) and \
                    float(current_value) == float(new_value):

                continue

            # just check again if values might match now
            if current_value_str == new_value_str:
                continue

            self.data[key] = new_value
            self.updated_items.append(key)

            if self.is_new is False:
                new_value_str = new_value_str.replace("\n", " ")
                log.info(f"{self.name.capitalize()} '{display_name}' attribute '{key}' changed from "
                          f"'{current_value_str}' to '{new_value_str}'")

            self.resolve_relations()

    def get_display_name(self, data=None, including_second_key=False):
        """
        return a name as string of this object based on primary/secondary key

        Parameters
        ----------
        data: dict
            optional data dictionary to format name from if object is not initialized
        including_second_key: bool
            if True adds second key if object has one

        Returns
        -------
        str: name of object
        """

        this_data_set = data
        if data is None:
            this_data_set = self.data

        if this_data_set is None:
            return None

        my_name = this_data_set.get(self.primary_key)

        secondary_key = getattr(self, "secondary_key", None)
        enforce_secondary_key = getattr(self, "enforce_secondary_key", False)

        if my_name is not None and secondary_key is not None and \
                (enforce_secondary_key is True or including_second_key is True):

            secondary_key_value = this_data_set.get(secondary_key)
            org_secondary_key_value = str(secondary_key_value)

            if isinstance(secondary_key_value, NetBoxObject):
                secondary_key_value = secondary_key_value.get_display_name()

            if isinstance(secondary_key_value, dict):
                secondary_key_value = self.get_display_name(data=secondary_key_value)

            if secondary_key_value is None:
                log.error(f"Unable to determine second key '{secondary_key}' for {self.name} '{my_name}', "
                          f"got: {org_secondary_key_value}")
                log.error("This could cause serious errors and lead to wrongly assigned object relations!!!")

            my_name = f"{my_name} ({secondary_key_value})"

        return my_name

    def resolve_relations(self):
        """
        Resolve object relations for this object. Substitute a dict of data with a id with the instantiated
        reference of this object
        """

        for key, data_type in self.data_model.items():

            if self.data.get(key) is None:
                continue

            if key.startswith("primary_ip"):
                data_type = NBIPAddress

            # continue if data_type is not an NetBox object
            if data_type not in NetBoxObject.__subclasses__() + NBObjectList.__subclasses__():
                continue

            data_value = self.data.get(key)

            if data_type in NBObjectList.__subclasses__():

                resolved_object_list = data_type()
                for item in data_value:

                    if isinstance(item, data_type.member_type):
                        item_object = item
                    else:
                        item_object = self.inventory.get_by_data(data_type.member_type, data=item)

                    if item_object is not None:
                        resolved_object_list.append(item_object)

                resolved_data = resolved_object_list

            else:
                if data_value is None:
                    continue

                if isinstance(data_value, NetBoxObject):
                    resolved_data = data_value
                else:
                    data_to_find = None
                    if isinstance(data_value, int):
                        data_to_find = {"id": data_value}
                    elif isinstance(data_value, dict):
                        data_to_find = data_value

                    resolved_data = self.inventory.get_by_data(data_type, data=data_to_find)

            if resolved_data is not None:
                self.data[key] = resolved_data
            else:
                log.error(f"Problems resolving relation '{key}' for object '{self.get_display_name()}' and "
                          f"value '{data_value}'")

    def get_dependencies(self):
        """
        returns a list of NetBoxObject sub classes this object depends on

        Returns
        -------
        list: of NetBoxObject sub classes
        """

        r = [x for x in self.data_model.values() if x in NetBoxObject.__subclasses__()]
        r.extend([x.member_type for x in self.data_model.values() if x in NBObjectList.__subclasses__()])

        return r

    def get_tags(self):
        """
        returns a list of strings of tag names

        Returns
        -------
        list: of strings of tga names
        """

        return [x.get_display_name() for x in self.data.get("tags", list())]

    def compile_tags(self, tags, remove=False):
        """

        Parameters
        ----------
        tags: (str, list, dict, NBTag)
            tags to parse and add/remove to/from current list of object tags
        remove: bool
            True if tags shall be removed, otherwise they will be added

        Returns
        -------
        NBTagList: with added/removed tags
        """

        if tags is None or NBTagList not in self.data_model.values():
            return

        # list of parsed tag strings
        sanitized_tag_strings = list()

        log.debug2(f"Compiling TAG list")

        new_tag_list = NBTagList()

        def extract_tags(this_tags):
            if isinstance(this_tags, NBTag):
                sanitized_tag_strings.append(this_tags.get_display_name())
            elif isinstance(this_tags, str):
                sanitized_tag_strings.append(this_tags)
            elif isinstance(this_tags, dict) and this_tags.get("name") is not None:
                sanitized_tag_strings.append(this_tags.get("name"))

        if isinstance(tags, list):
            for tag in tags:
                extract_tags(tag)
        else:
            extract_tags(tags)

        # current list of tag strings
        current_tag_strings = self.get_tags()

        new_tags = list()
        removed_tags = list()

        for tag_name in sanitized_tag_strings:

            # add tag
            if tag_name not in current_tag_strings and remove is False:

                tag = self.inventory.add_update_object(NBTag, data={"name": tag_name})

                new_tags.append(tag)

            if tag_name in current_tag_strings and remove is True:

                tag = self.inventory.get_by_data(NBTag, data={"name": tag_name})

                removed_tags.append(tag)

        current_tags = grab(self, "data.tags", fallback=NBTagList())

        if len(new_tags) > 0:

            for tag in new_tags + current_tags:
                new_tag_list.append(tag)

        elif len(removed_tags) > 0:

            for tag in current_tags:
                if tag not in removed_tags:
                    new_tag_list.append(tag)
        else:
            new_tag_list = current_tags

        return new_tag_list

    def update_tags(self, tags, remove=False):
        """
        Update list of object tags

        Parameters
        ----------
        tags: (str, list, dict, NBTag)
            tags to parse and add/remove to/from current list of object tags
        remove: bool
            True if tags shall be removed, otherwise they will be added

        Returns
        -------
        None
        """

        if tags is None or NBTagList not in self.data_model.values():
            return

        action = "Adding" if remove is False else "Removing"

        log.debug2(f"{action} Tags: {tags}")

        current_tags = grab(self, "data.tags", fallback=NBTagList())

        new_tags = self.compile_tags(tags, remove=remove)

        if str(current_tags.get_display_name()) != str(new_tags.get_display_name()):

            self.data["tags"] = new_tags
            self.updated_items.append("tags")

            log.info(f"{self.name.capitalize()} '{self.get_display_name()}' attribute 'tags' changed from "
                      f"'{current_tags.get_display_name()}' to '{new_tags.get_display_name()}'")

    def add_tags(self, tags_to_add):
        """
        Add tag(s) to object

        Parameters
        ----------
        tags_to_add: (str, list, dict, NBTag)
            tags to parse and add to current list of object tags

        Returns
        -------
        None
        """

        self.update_tags(tags_to_add)

    def remove_tags(self, tags_to_remove):
        """
        remove tag(s) to object

        Parameters
        ----------
        tags_to_remove: (str, list, dict, NBTag)
            tags to parse and remove from current list of object tags

        Returns
        -------
        None
        """

        self.update_tags(tags_to_remove, remove=True)

    def compile_vlans(self, vlans):
        """
        Read list of VLANs and return a new and sanitized list of VLANs

        Parameters
        ----------
        vlans: list of (dict, NBVLAN)
            list of VLANs that should be in the returned list

        Returns
        -------
        NBVLANList: of parsed VLANs
        """

        if vlans is None or NBVLANList not in self.data_model.values():
            return

        if not isinstance(vlans, list):
            raise ValueError("Value for vlans must be a list")

        log.debug2(f"Compiling VLAN list")
        new_vlan_list = NBVLANList()

        for vlan in vlans:

            if isinstance(vlan, NBVLAN):
                new_vlan_object = vlan
            elif isinstance(vlan, dict):
                new_vlan_object = self.inventory.add_update_object(NBVLAN, data=vlan, source=self.source)
            else:
                log.error(f"Unable to parse provided VLAN data: {vlan}")
                continue

            # VLAN already in list, must have been submitted twice
            if new_vlan_object in new_vlan_list:
                continue

            new_vlan_list.append(new_vlan_object)

        return new_vlan_list

    def unset_attribute(self, attribute_name=None):
        """
        Unset a certain attribute. This will delete the value of this attribute in NetBox on the first run of
        updating data in NetBox

        Parameters
        ----------
        attribute_name: str
            name of the attribute to unset

        Returns
        -------
        None
        """

        if attribute_name is None:
            return

        if attribute_name not in self.data_model.keys():
            log.error(f"Found undefined data model key '{attribute_name}' for object '{self.__class__.__name__}'")
            return

        # mark attribute to unset, this way it will be deleted in NetBox before any other updates are performed
        log.info(f"Setting attribute '{attribute_name}' for '{self.get_display_name()}' to None")
        self.unset_items.append(attribute_name)

    def get_nb_reference(self):
        """
        return reference of how this object is referenced in NetBox

        Returns
        -------
        (None, int): if NetBox ID is 0 (new object) return None otherwise return ID
        """

        if self.nb_id == 0:
            return None

        return self.nb_id


class NBObjectList(list):
    """
    Base class of listed NetBox objects. Extends list(). Currently used for tags and untagged VLANs

    Mandatory attributes:
        member_type: NetBoxObject sub class
            defines the type objects contained in this type of list
    """

    def get_display_name(self):

        return sorted([x.get_display_name() for x in self])


class NBTag(NetBoxObject):
    name = "tag"
    api_path = "extras/tags"
    primary_key = "name"
    prune = False
    data_model = {
        "name": 100,
        "slug": 100,
        "color": 6,
        "description": 200
    }


class NBTagList(NBObjectList):
    member_type = NBTag

    def get_nb_reference(self):
        """
            return None if one tag is unresolvable

            Once the tag was created in NetBox it can be assigned to objects
        """
        return_list = list()
        for tag in self:
            if tag.nb_id == 0:
                return None

            return_list.append({"name": tag.get_display_name()})

        return return_list


class NBTenant(NetBoxObject):
    name = "tenant"
    api_path = "tenancy/tenants"
    primary_key = "name"
    prune = False
    data_model = {
        "name": 30,
        "slug": 50,
        "comments": str,
        "description": 200,
        "tags": NBTagList
    }


class NBSite(NetBoxObject):
    name = "site"
    api_path = "dcim/sites"
    primary_key = "name"
    prune = False
    data_model = {
        "name": 50,
        "slug": 50,
        "comments": str,
        "tenant": NBTenant,
        "tags": NBTagList
    }


class NBVRF(NetBoxObject):
    name = "VRF"
    api_path = "ipam/vrfs"
    primary_key = "name"
    prune = False
    data_model = {
        "name": 50,
        "description": 200,
        "tenant": NBTenant,
        "tags": NBTagList
    }


class NBVLAN(NetBoxObject):
    name = "VLAN"
    api_path = "ipam/vlans"
    primary_key = "vid"
    secondary_key = "name"
    enforce_secondary_key = True
    prune = False
    data_model = {
        "vid": int,
        "name": 64,
        "site": NBSite,
        "description": 200,
        "tenant": NBTenant,
        "tags": NBTagList
    }

    def get_display_name(self, data=None, including_second_key=False):
        """
            for VLANs we change the behavior of display name.

            It is important to get the VLAN for the same site. And we don't want
            to change the name if it's already in NetBox.

            Even though the secondary key is the name we change it to site. If site
            is not present we fall back to name.
        """

        # run just to check input data
        my_name = super().get_display_name(data=data, including_second_key=including_second_key)

        this_data_set = data
        if data is None:
            this_data_set = self.data

        # we use "site" as secondary key, otherwise fall back to "name"
        this_site = this_data_set.get("site")
        if this_site is not None:
            vlan_id = this_data_set.get(self.primary_key)

            site_name = None
            if isinstance(this_site, NetBoxObject):
                site_name = this_site.get_display_name()

            if isinstance(this_site, dict):
                site_name = this_site.get("name")

            if site_name is not None:
                my_name = f"{vlan_id} ({site_name})"

        return my_name

    def update(self, data=None, read_from_netbox=False, source=None):

        # don't change the name of the VLAN if it already exists
        if read_from_netbox is False and grab(self, "data.name") is not None:
            data["name"] = grab(self, "data.name")

        super().update(data=data, read_from_netbox=read_from_netbox, source=source)


class NBVLANList(NBObjectList):
    member_type = NBVLAN

    def get_nb_reference(self):
        """
            return None if one VLAN is unresolvable

            Once the VLAN was created in NetBox it can be assigned to objects
        """
        return_list = list()
        for vlan in self:
            if vlan.nb_id == 0:
                return None

            return_list.append(vlan.nb_id)

        return return_list


class NBPrefix(NetBoxObject):
    name = "IP prefix"
    api_path = "ipam/prefixes"
    primary_key = "prefix"
    prune = False
    data_model = {
        "prefix": [IPv4Network, IPv6Network],
        "site": NBSite,
        "tenant": NBTenant,
        "vlan": NBVLAN,
        "vrf": NBVRF,
        "description": 200,
        "tags": NBTagList
    }

    def update(self, data=None, read_from_netbox=False, source=None):

        # prefixes are parsed into ip_networks
        data_prefix = data.get(self.primary_key)
        if data_prefix is not None and not isinstance(data_prefix, (IPv4Network, IPv6Network)):
            try:
                data[self.primary_key] = ip_network(data_prefix)
            except ValueError as e:
                log.error(f"Failed to parse {self.name} '{data_prefix}': {e}")
                return

        super().update(data=data, read_from_netbox=read_from_netbox, source=source)

        if read_from_netbox is False:
            raise ValueError(f"Adding {self.name} by this program is currently not implemented.")


class NBManufacturer(NetBoxObject):
    name = "manufacturer"
    api_path = "dcim/manufacturers"
    primary_key = "name"
    prune = False
    data_model = {
        "name": 50,
        "slug": 50,
        "description": 200
    }


class NBDeviceType(NetBoxObject):
    name = "device type"
    api_path = "dcim/device-types"
    primary_key = "model"
    prune = False
    data_model = {
        "model": 50,
        "slug": 50,
        "part_number": 50,
        "description": 200,
        "manufacturer": NBManufacturer,
        "tags": NBTagList
    }


class NBPlatform(NetBoxObject):
    name = "platform"
    api_path = "dcim/platforms"
    primary_key = "name"
    prune = False
    data_model = {
        "name": 100,
        "slug": 100,
        "manufacturer": NBManufacturer,
        "description": 200
    }


class NBClusterType(NetBoxObject):
    name = "cluster type"
    api_path = "virtualization/cluster-types"
    primary_key = "name"
    prune = False
    data_model = {
        "name": 50,
        "slug": 50,
        "description": 200
    }


class NBClusterGroup(NetBoxObject):
    name = "cluster group"
    api_path = "virtualization/cluster-groups"
    primary_key = "name"
    prune = False
    data_model = {
        "name": 50,
        "slug": 50,
        "description": 200
    }


class NBDeviceRole(NetBoxObject):
    name = "device role"
    api_path = "dcim/device-roles"
    primary_key = "name"
    prune = False
    data_model = {
        "name": 50,
        "slug": 50,
        "color": 6,
        "description": 200,
        "vm_role": bool
    }


class NBCluster(NetBoxObject):
    name = "cluster"
    api_path = "virtualization/clusters"
    primary_key = "name"
    prune = False
    data_model = {
        "name": 100,
        "comments": str,
        "type": NBClusterType,
        "group": NBClusterGroup,
        "site": NBSite,
        "tags": NBTagList
    }


class NBDevice(NetBoxObject):
    """
    data key "primary_ip*" has "object" assigned as valid data type.
    This has been done to avoid circular dependencies.

    would be happy if someone could come up with a proper solution
    """

    name = "device"
    api_path = "dcim/devices"
    primary_key = "name"
    secondary_key = "site"
    prune = True
    data_model = {
        "name": 64,
        "device_type": NBDeviceType,
        "device_role": NBDeviceRole,
        "platform": NBPlatform,
        "serial": 50,
        "site": NBSite,
        "status": ["offline", "active", "planned", "staged", "failed", "inventory", "decommissioning"],
        "cluster": NBCluster,
        "asset_tag": 50,
        "primary_ip4": object,
        "primary_ip6": object,
        "tags": NBTagList,
        "tenant": NBTenant
    }


class NBVM(NetBoxObject):
    """
    data key "primary_ip*" has "object" assigned as valid data type.
    This has been done to avoid circular dependencies.

    would be happy if someone could come up with a proper solution
    """

    name = "virtual machine"
    api_path = "virtualization/virtual-machines"
    primary_key = "name"
    secondary_key = "cluster"
    prune = True
    data_model = {
        "name": 64,
        "status": ["offline", "active", "planned", "staged", "failed", "decommissioning"],
        "cluster": NBCluster,
        "role": NBDeviceRole,
        "platform": NBPlatform,
        "vcpus": float,
        "memory": int,
        "disk": int,
        "comments": str,
        "primary_ip4": object,
        "primary_ip6": object,
        "tags": NBTagList,
        "tenant": NBTenant
    }


class NBVMInterface(NetBoxObject):
    name = "virtual machine interface"
    api_path = "virtualization/interfaces"
    primary_key = "name"
    secondary_key = "virtual_machine"
    enforce_secondary_key = True
    prune = True
    data_model = {
        "name": 64,
        "virtual_machine": NBVM,
        "enabled": bool,
        "mac_address": str,
        "mtu": int,
        "mode": ["access", "tagged", "tagged-all"],
        "untagged_vlan": NBVLAN,
        "tagged_vlans": NBVLANList,
        "description": 200,
        "tags": NBTagList
    }


class NBInterface(NetBoxObject):
    name = "interface"
    api_path = "dcim/interfaces"
    primary_key = "name"
    secondary_key = "device"
    enforce_secondary_key = True
    prune = True
    data_model = {
        "name": 64,
        "device": NBDevice,
        "label": 64,
        "type": ["virtual", "100base-tx", "1000base-t", "10gbase-t", "25gbase-x-sfp28", "40gbase-x-qsfpp", "other"],
        "enabled": bool,
        "mac_address": str,
        "mgmt_only": bool,
        "mtu": int,
        "mode": ["access", "tagged", "tagged-all"],
        "untagged_vlan": NBVLAN,
        "tagged_vlans": NBVLANList,
        "description": 200,
        "connection_status": bool,
        "tags": NBTagList
    }


class NBIPAddress(NetBoxObject):
    name = "IP address"
    api_path = "ipam/ip-addresses"
    primary_key = "address"
    is_primary = False
    prune = True
    data_model = {
        "address": str,
        "assigned_object_type": ["dcim.interface", "virtualization.vminterface"],
        "assigned_object_id": [NBInterface, NBVMInterface],
        "description": 200,
        "dns_name": 255,
        "tags": NBTagList,
        "tenant": NBTenant,
        "vrf": NBVRF
    }
    # add relation between two attributes
    data_model_relation = {
        "dcim.interface": NBInterface,
        "virtualization.vminterface": NBVMInterface,
        NBInterface: "dcim.interface",
        NBVMInterface: "virtualization.vminterface"
    }

    def resolve_relations(self):

        o_id = self.data.get("assigned_object_id")
        o_type = self.data.get("assigned_object_type")

        # this needs special treatment as the object type depends on a second model key
        if o_type is not None and o_type not in self.data_model.get("assigned_object_type"):

            log.error(f"Attribute 'assigned_object_type' for '{self.get_display_name()}' invalid: {o_type}")
            do_error_exit(f"Error while resolving relations for {self.get_display_name()}")

        if isinstance(o_id, int):
            self.data["assigned_object_id"] = self.inventory.get_by_id(self.data_model_relation.get(o_type), nb_id=o_id)

        super().resolve_relations()

    def update(self, data=None, read_from_netbox=False, source=None):

        object_type = data.get("assigned_object_type")
        assigned_object = data.get("assigned_object_id")

        # we got an object data structure where we have to find the object
        if read_from_netbox is False and assigned_object is not None:

            if not isinstance(assigned_object, NetBoxObject):

                data["assigned_object_id"] = \
                    self.inventory.add_update_object(self.data_model_relation.get(object_type), data=assigned_object)

            else:
                data["assigned_object_type"] = self.data_model_relation.get(type(assigned_object))

        super().update(data=data, read_from_netbox=read_from_netbox, source=source)

        # we need to tell NetBox which object type this is meant to be
        if "assigned_object_id" in self.updated_items:
            self.updated_items.append("assigned_object_type")

    def get_dependencies(self):
        """
            This is hard coded in here. Updated if data_model attribute changes!!!!
        """

        return [NBInterface, NBVMInterface, NBTag, NBTenant, NBVRF]

# EOF
