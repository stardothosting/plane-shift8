# Python imports
import math
from collections import defaultdict
from collections.abc import Sequence

# Django imports
from django.db.models import Count, F, Window
from django.db.models.functions import RowNumber

# Third party imports
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

# Module imports


class InitialGrouperPaginator(object):
    """This class generate the initial grouping of the queryset"""

    max_limit = 50

    def __init__(
        self,
        queryset,
        group_by_field_name,
        group_by_fields,
        count_queryset,
        order_by=None,
    ) -> None:
        # Set the queryset
        self.queryset = queryset
        # Set the group by field name
        self.group_by_field_name = group_by_field_name
        # Set the group by fields
        self.group_by_fields = group_by_fields
        # Set the count queryset
        self.count_queryset = count_queryset
        # Set the key
        self.desc = True if order_by and order_by.startswith("-") else False
        # Key tuple and remove `-` if descending order by
        self.key = (
            order_by
            if order_by is None or isinstance(order_by, (list, tuple, set))
            else (order_by[1::] if order_by.startswith("-") else order_by,)
        )

    def __get_total_queryset(self):
        # Get total items for each group
        return (
            self.count_queryset.values(self.group_by_field_name)
            .annotate(
                count=Count(
                    "id",
                    distinct=True,
                )
            )
            .order_by()
        )

    def __get_total_dict(self):
        # Convert the total into dictionary of keys as group name and value as the total
        total_group_dict = {}
        for group in self.__get_total_queryset():
            total_group_dict[str(group.get(self.group_by_field_name))] = (
                total_group_dict.get(
                    str(group.get(self.group_by_field_name)), 0
                )
                + (1 if group.get("count") == 0 else group.get("count"))
            )
        return total_group_dict

    def __get_field_dict(self):
        # Create a field dictionary
        total_group_dict = self.__get_total_dict()
        return {
            str(field): {
                "results": [],
                "total_results": total_group_dict.get(str(field), 0),
            }
            for field in self.group_by_fields
        }

    def __result_already_added(self, result, group):
        # Check if the result is already added then add it
        for existing_issue in group:
            if existing_issue["id"] == result["id"]:
                return True
        return False

    def __query_multi_grouper(self, results):
        # Grouping for m2m values
        total_group_dict = self.__get_total_dict()

        # Preparing a dict to keep track of group IDs associated with each entity ID
        result_group_mapping = defaultdict(set)
        # Preparing a dict to group result by group ID
        grouped_by_field_name = defaultdict(list)

        # Iterate over results to fill the above dictionaries
        for result in results:
            result_id = result["id"]
            group_id = result[self.group_by_field_name]
            result_group_mapping[str(result_id)].add(str(group_id))

        # Adding group_ids key to each issue and grouping by group_name
        for result in results:
            result_id = result["id"]
            group_ids = list(result_group_mapping[str(result_id)])
            result[self.FIELD_MAPPER.get(self.group_by_field_name)] = (
                [] if "None" in group_ids else group_ids
            )
            # If a result belongs to multiple groups, add it to each group
            for group_id in group_ids:
                if not self.__result_already_added(
                    result, grouped_by_field_name[group_id]
                ):
                    grouped_by_field_name[group_id].append(result)

        # Convert grouped_by_field_name back to a list for each group
        processed_results = {
            str(group_id): {
                "results": issues,
                "total_results": total_group_dict.get(str(group_id)),
            }
            for group_id, issues in grouped_by_field_name.items()
        }

        # Return the processed results
        return processed_results

    def __query_grouper(self, results):
        # Grouping for values that are not m2m
        processed_results = self.__get_field_dict()
        for result in results:
            group_value = str(result.get(self.group_by_field_name))
            if group_value in processed_results:
                processed_results[str(group_value)]["results"].append(result)
        # Return the processed results
        return processed_results

    # Get the results
    def get_result(self, limit=50, is_multi_grouper=False, on_results=None):
        # Get the min from limit and max limit
        limit = min(limit, self.max_limit)
        # Get the queryset
        queryset = self.queryset
        # Create window for all the groups
        queryset = queryset.annotate(
            row_number=Window(
                expression=RowNumber(),
                partition_by=[F(self.group_by_field_name)],
                order_by=(
                    (
                        F(*self.key).desc(
                            nulls_last=True
                        )  # order by desc if desc is set
                        if self.desc
                        else F(*self.key).asc(
                            nulls_last=True
                        )  # Order by asc if set
                    ),
                    F("created_at").desc(),
                ),
            )
        )

        # Filter the results by row number
        results = queryset.filter(row_number__lt=limit + 1).order_by(
            (
                F(*self.key).desc(nulls_last=True)
                if self.desc
                else F(*self.key).asc(nulls_last=True)
            ),
            F("created_at").desc(),
        )

        # Process the results
        if on_results:
            results = on_results(results)

        # Process results
        if results:
            # Check if the results are multi grouper
            if is_multi_grouper:
                processed_results = self.__query_multi_grouper(results=results)
            else:
                processed_results = self.__query_grouper(results=results)
        else:
            processed_results = {}

        response = Response(
            {
                "results": processed_results,
                "total_count": self.count_queryset.count(),
            }
        )

        return response
