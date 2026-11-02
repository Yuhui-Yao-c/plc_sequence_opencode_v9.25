import unittest
from process_semantics import tag
class TestActionTriage(unittest.TestCase):
 def test_state_only_not_promoted(self):
  self.assertEqual(tag({'状态':'adjacency_hypothesis','动作':'未确认阶段','动作描述':'相邻已观察状态'})[1],'review_only')
 def test_action_event_prioritized(self):
  self.assertEqual(tag({'状态':'action_event_unverified','动作':'AxisMove','动作描述':'Z轴下降'})[1],'needs_boundary')
 def test_generic_coil_not_action(self):
  self.assertEqual(tag({'状态':'action_event_unverified','动作':'coil','动作描述':''})[1],'review_only')
 def test_process_wait_prioritized(self):
  self.assertEqual(tag({'状态':'action_event_unverified','动作':'等待Ready','动作描述':'放料许可'})[1],'needs_boundary')
 def test_numerical_neighbor_not_proof(self):
  self.assertEqual(tag({'状态':'adjacency_hypothesis','动作':'轴移动','动作描述':'电机上升'})[1],'review_only')
if __name__=='__main__':unittest.main()
