# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
from unittest import mock

from absl.testing import absltest
import grpc
from xmanager.xm_cloud import dashboard

os.environ['GIT_PYTHON_REFRESH'] = 'quiet'

_CHART_ID = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
_OTHER_CHART_ID = '10d38935-d2bf-4727-b887-26088bf05446'


class DashboardTest(absltest.TestCase):

  def setUp(self):
    super().setUp()
    self.mock_stub = mock.MagicMock()
    self.enter_context(
        mock.patch.object(
            dashboard,
            'get_dashboard_service_stub',
            return_value=self.mock_stub,
        )
    )

  def test_create_plot(self):
    plot = dashboard.create_plot(title='Test Plot')
    self.assertIsInstance(plot, dashboard.Plot)
    self.assertEqual(plot.title, 'Test Plot')

  def test_create_chart(self):
    plot = dashboard.create_plot(title='Metric Plot')
    chart = dashboard.create_chart(
        title='Accuracy Chart',
        experiment_id=123,
        plots=[plot],
    )
    self.assertIsInstance(chart, dashboard.Chart)
    self.assertEqual(chart.title, 'Accuracy Chart')
    self.assertEqual(chart.experiment_id, 123)
    self.assertEqual(chart.experiment_name, 'experiments/123')
    self.assertLen(chart.plots, 1)
    self.assertEqual(chart.plots[0].title, 'Metric Plot')

  def test_create_chart_empty_plots_raises(self):
    with self.assertRaises(ValueError):
      dashboard.create_chart(
          title='Empty Chart',
          experiment_id=123,
          plots=[],
      )

  def test_create_dashboard(self):
    plot = dashboard.create_plot(title='Loss Plot')
    chart = dashboard.create_chart(
        title='Loss Chart',
        experiment_id=123,
        plots=[plot],
    )
    dashboard_proto = dashboard.dashboard_pb2.Dashboard(
        name='dashboards/456', title='Main Dashboard'
    )
    self.mock_stub.CreateDashboard.return_value = dashboard_proto

    dash = dashboard.create_dashboard(
        title='Main Dashboard',
        charts=[chart],
    )
    self.assertIsInstance(dash, dashboard.Dashboard)
    self.assertEqual(dash.name, 'dashboards/456')
    self.assertEqual(dash.id, '456')
    self.assertEqual(dash.title, 'Main Dashboard')
    self.assertLen(dash.charts, 1)
    self.assertEqual(dash.charts[0].experiment_id, 123)

  def test_create_dashboard_empty_charts_raises(self):
    with self.assertRaises(ValueError):
      dashboard.create_dashboard(title='Empty Dash', charts=[])

  def test_dashboard_update_and_no_delete(self):
    dashboard_proto = dashboard.dashboard_pb2.Dashboard(
        name='dashboards/789', title='Old Title'
    )
    response = dashboard.dashboard_pb2.Dashboard(
        name='dashboards/789', title='Saved Title'
    )
    dash = dashboard.Dashboard(dashboard_proto, stub=self.mock_stub)

    self.assertFalse(hasattr(dash, 'delete'))
    self.assertFalse(hasattr(dash, 'delete_dashboard'))

    self.mock_stub.UpdateDashboard.return_value = response
    dash.update(title='Updated Title')
    self.mock_stub.UpdateDashboard.assert_called_once()
    request = self.mock_stub.UpdateDashboard.call_args[0][0]
    self.assertEqual(list(request.update_mask.paths), ['title'])
    self.assertEqual(request.dashboard.name, 'dashboards/789')
    self.assertEqual(request.dashboard.title, 'Updated Title')
    self.assertEqual(dash.title, 'Saved Title')

  @absltest.skipIf(
      isinstance(dashboard.dashboard_pb2, dashboard._DummyProto),
      'Dashboard protobufs are required to exercise field descriptors.',
  )
  def test_dashboard_update_repeated_field(self):
    dashboard_proto = dashboard.dashboard_pb2.Dashboard(
        name='dashboards/789', chart_ids=[_CHART_ID]
    )
    response = dashboard.dashboard_pb2.Dashboard(
        name='dashboards/789', title='Saved Title', chart_ids=[_OTHER_CHART_ID]
    )
    dash = dashboard.Dashboard(dashboard_proto, stub=self.mock_stub)
    self.mock_stub.UpdateDashboard.return_value = response

    dash.update(chart_ids=[_OTHER_CHART_ID])

    self.mock_stub.UpdateDashboard.assert_called_once()
    request = self.mock_stub.UpdateDashboard.call_args[0][0]
    self.assertEqual(list(request.update_mask.paths), ['chart_ids'])
    self.assertEqual(list(request.dashboard.chart_ids), [_OTHER_CHART_ID])
    self.assertEqual(list(dashboard_proto.chart_ids), [_OTHER_CHART_ID])
    self.assertEqual(dash.title, 'Saved Title')

  def test_dashboard_update_repeated_field_without_descriptor(self):
    proto_module = dashboard._DummyProto()
    with mock.patch.object(dashboard, 'dashboard_pb2', proto_module):
      plot = dashboard.create_plot(title='Plot')
      chart = dashboard.create_chart(
          title='Chart', experiment_id=123, plots=[plot]
      )
    dashboard_proto = proto_module.Dashboard(
        name='dashboards/789', title='Title', charts=[]
    )
    dash = dashboard.Dashboard(dashboard_proto, stub=self.mock_stub)
    self.mock_stub.UpdateDashboard.return_value = dashboard_proto

    with mock.patch.object(dashboard, 'api_pb2', proto_module):
      dash.update(charts=[chart])

    self.mock_stub.UpdateDashboard.assert_called_once()
    request = self.mock_stub.UpdateDashboard.call_args[0][0]
    self.assertEqual(list(request.update_mask.paths), ['charts'])
    self.assertEqual(request.dashboard.charts, [chart.to_proto()])
    self.assertEqual(dash.charts[0].title, 'Chart')

  def test_dashboard_update_unknown_field_raises(self):
    dashboard_proto = dashboard.dashboard_pb2.Dashboard()
    dash = dashboard.Dashboard(dashboard_proto, stub=self.mock_stub)
    with self.assertRaises(ValueError):
      dash.update(invalid_field='foo')
    self.mock_stub.UpdateDashboard.assert_not_called()

  def test_dashboard_update_repeated_field_non_iterable_raises(self):
    dashboard_proto = dashboard._DummyProto().Dashboard()
    dash = dashboard.Dashboard(dashboard_proto, stub=self.mock_stub)
    with self.assertRaises(TypeError):
      dash.update(charts=123)
    self.mock_stub.UpdateDashboard.assert_not_called()

  @absltest.skipIf(
      isinstance(dashboard.dashboard_pb2, dashboard._DummyProto),
      'Dashboard protobufs are required to exercise field descriptors.',
  )
  def test_dashboard_update_with_descriptor(self):
    dashboard_proto = dashboard.dashboard_pb2.Dashboard(name='dashboards/789')
    dash = dashboard.Dashboard(dashboard_proto, stub=self.mock_stub)
    self.mock_stub.UpdateDashboard.return_value = dashboard_proto

    dash.update(
        title='Updated Title',
        description='Dashboard description',
        chart_ids=[_CHART_ID],
    )

    self.mock_stub.UpdateDashboard.assert_called_once()
    request = self.mock_stub.UpdateDashboard.call_args[0][0]
    self.assertEqual(
        list(request.update_mask.paths), ['title', 'description', 'chart_ids']
    )
    self.assertEqual(request.dashboard.title, 'Updated Title')
    self.assertEqual(request.dashboard.description, 'Dashboard description')
    self.assertEqual(list(request.dashboard.chart_ids), [_CHART_ID])
    self.assertEqual(dash.title, 'Updated Title')

    with self.assertRaises(ValueError):
      dash.update(unknown='bad')
    with self.assertRaises(TypeError):
      dash.update(chart_ids=123)
    self.mock_stub.UpdateDashboard.assert_called_once()


class GetDashboardServiceStubTest(absltest.TestCase):

  def setUp(self):
    super().setUp()
    dashboard.get_dashboard_service_stub.cache_clear()

  def tearDown(self):
    super().tearDown()
    dashboard.get_dashboard_service_stub.cache_clear()

  @mock.patch.object(grpc, 'insecure_channel', autospec=True)
  def test_localhost_uses_insecure_channel(self, mock_insecure_channel):
    with mock.patch.dict(
        os.environ, {'XMANAGER_DASHBOARD_ENDPOINT': 'localhost:8080'}
    ):
      stub = dashboard.get_dashboard_service_stub()
      mock_insecure_channel.assert_called_once_with('localhost:8080')
      self.assertIsNotNone(stub)

  @mock.patch.object(grpc, 'secure_channel', autospec=True)
  @mock.patch.object(grpc, 'ssl_channel_credentials', autospec=True)
  def test_remote_uses_secure_channel(
      self, mock_ssl_creds, mock_secure_channel
  ):
    with mock.patch.dict(
        os.environ,
        {'XMANAGER_DASHBOARD_ENDPOINT': 'dns:///remote.example.com:443'},
    ):
      stub = dashboard.get_dashboard_service_stub()
      mock_ssl_creds.assert_called_once()
      mock_secure_channel.assert_called_once_with(
          'dns:///remote.example.com:443', mock_ssl_creds.return_value
      )
      self.assertIsNotNone(stub)


if __name__ == '__main__':
  absltest.main()
